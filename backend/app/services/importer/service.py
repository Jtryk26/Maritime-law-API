"""Importservice — orkestrerer hele pipelinen.

Denne klasse indeholder bevidst ingen forretningslogik for hentning,
klassifikation, kategorisering eller persistering. Den kalder de
tjenester, der hver ejer sit område::

    kilde -> normalisering -> relevans -> kategorisering -> repositorium

Fejlhåndtering
==============
Ét dårligt dokument må ikke vælte en hel import. Hvert dokument
behandles i sin egen transaktion: fejler det, rulles netop dét dokument
tilbage, fejlen registreres på importkørslen, og behandlingen fortsætter.

Bryder mange dokumenter i træk, standses kørslen alligevel — så er noget
galt med kilden snarere end med det enkelte dokument.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import ImportRun, ImportStatus
from app.services.categorization.base import CategorizationEngine, CategorizationResult
from app.services.relevance.base import RelevanceEngine
from app.services.retsinformation.base import (
    DocumentNotFoundError,
    DocumentRef,
    NormalizedDocument,
    PermanentSourceError,
    SourceClient,
    SourceError,
)

from .repository import DocumentRepository

logger = get_logger(__name__)

__all__ = ["DocumentOutcome", "ImportService", "ImportSummary", "Outcome"]

#: Maks. antal fejldetaljer der gemmes på importkørslen.
MAX_STORED_ERRORS = 50


class Outcome(str, enum.Enum):
    """Hvad der skete med ét enkelt kildedokument.

    Bruges af kaldere, der styrer en kø (se `app.services.backfill`) og
    skal kunne skelne en endelig afvisning fra en fejl, det er værd at
    forsøge igen.
    """

    CREATED = "CREATED"
    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"
    #: Under lagringstærsklen — ikke maritimt relevant. Endeligt.
    REJECTED = "REJECTED"
    FAILED = "FAILED"


#: Fejltyper hvor gentagne forsøg er nytteløse.
_PERMANENT_ERRORS: tuple[type[Exception], ...] = (
    DocumentNotFoundError,
    PermanentSourceError,
)


@dataclass(slots=True)
class DocumentOutcome:
    """Resultatet for ét dokument i en importkørsel."""

    source_id: str
    outcome: Outcome
    error_type: str | None = None
    error: str | None = None
    #: False for permanente fejl (dokumentet findes ikke, 4xx m.v.).
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "outcome": self.outcome.value,
            "error_type": self.error_type,
            "error": self.error,
            "retryable": self.retryable,
        }


@dataclass(slots=True)
class ImportSummary:
    """Resultatet af en importkørsel."""

    import_run_id: int
    checked: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    failed: int = 0
    status: str = ImportStatus.COMPLETED.value
    errors: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: Pr-dokument udfald i behandlingsrækkefølge. Tællerne ovenfor er
    #: aggregatet; denne liste gør det muligt at afgøre hvad der skete
    #: med et *bestemt* source_id.
    outcomes: list[DocumentOutcome] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_run_id": self.import_run_id,
            "documents_checked": self.checked,
            "documents_created": self.created,
            "documents_updated": self.updated,
            "documents_unchanged": self.unchanged,
            "documents_rejected": self.rejected,
            "documents_failed": self.failed,
            "status": self.status,
            "errors": self.errors,
        }

    def outcome_map(self) -> dict[str, DocumentOutcome]:
        """Udfald pr. source_id. Sidste udfald vinder ved dubletter."""
        return {o.source_id: o for o in self.outcomes}


class ImportService:
    """Synkroniserer dokumenter fra en kilde ind i den lokale database."""

    def __init__(
        self,
        session: Session,
        *,
        client: SourceClient,
        relevance_engine: RelevanceEngine,
        categorization_engine: CategorizationEngine,
        store_min_score: int | None = None,
        max_consecutive_failures: int | None = None,
    ) -> None:
        settings = get_settings()
        self.session = session
        self.client = client
        self.relevance_engine = relevance_engine
        self.categorization_engine = categorization_engine
        self.repository = DocumentRepository(session)
        self.store_min_score = (
            store_min_score if store_min_score is not None else settings.import_store_min_score
        )
        self.max_consecutive_failures = (
            max_consecutive_failures
            if max_consecutive_failures is not None
            else settings.import_max_consecutive_failures
        )

    # -- Offentlig API ------------------------------------------------------

    def run(
        self,
        *,
        since: date | None = None,
        trigger: str = "manual",
        limit: int | None = None,
        explicit_ids: Iterable[str] | None = None,
    ) -> ImportSummary:
        """Kører en fuld import.

        Args:
            since: Hent kun dokumenter ændret fra denne dato.
            trigger: Hvordan importen blev startet ("manual", "api", "cli").
            limit: Behandl højst dette antal dokumenter. Bruges til test.
            explicit_ids: Hent netop disse kilde-id'er (accessionsnumre) i
                stedet for at spørge ændringsfeeden. Nødvendigt for
                historisk efterindlæsning, da feeden kun rækker
                ti dage tilbage. Udelukker `since`.
        """
        explicit = list(explicit_ids) if explicit_ids is not None else None
        if explicit is not None and since is not None:
            raise ValueError("explicit_ids og since kan ikke kombineres.")
        if explicit is not None and not explicit:
            raise ValueError("explicit_ids var tom. Angiv mindst ét kilde-id.")

        run = self._start_run(trigger)
        summary = ImportSummary(import_run_id=run.id, started_at=run.started_at)

        # Kategorierne synkroniseres fra konfiguration ved hver kørsel, så
        # taksonomiændringer slår igennem uden en særskilt kommando.
        self.repository.sync_categories(self.categorization_engine.definitions())
        self.session.commit()

        logger.info(
            "import.started",
            extra={
                "import_run_id": run.id,
                "client": getattr(self.client, "kind", "ukendt"),
                "trigger": trigger,
                "since": since.isoformat() if since else "alle",
                "explicit_ids": len(explicit) if explicit is not None else 0,
            },
        )

        try:
            refs = list(self._discover(since, explicit))
        except Exception as exc:
            self._fail_run(run, f"Kunne ikke hente dokumentliste fra kilden: {exc}")
            summary.status = ImportStatus.FAILED.value
            summary.finished_at = run.finished_at
            logger.exception("import.discovery.failed", extra={"import_run_id": run.id})
            return summary

        if limit is not None:
            refs = refs[:limit]

        logger.info(
            "import.discovered", extra={"import_run_id": run.id, "documents": len(refs)}
        )

        consecutive_failures = 0

        for ref in refs:
            summary.checked += 1
            try:
                self._process_one(ref, run.id, summary)
                self.session.commit()
                consecutive_failures = 0
            except Exception as exc:
                # Rul netop dette dokument tilbage; kørslen fortsætter.
                self.session.rollback()
                summary.failed += 1
                consecutive_failures += 1
                self._record_error(summary, ref.source_id, exc)
                summary.outcomes.append(
                    DocumentOutcome(
                        source_id=ref.source_id,
                        outcome=Outcome.FAILED,
                        error_type=type(exc).__name__,
                        error=str(exc)[:500],
                        retryable=not isinstance(exc, _PERMANENT_ERRORS),
                    )
                )
                logger.warning(
                    "import.document.failed",
                    extra={
                        "source_id": ref.source_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    },
                )

                if consecutive_failures >= self.max_consecutive_failures:
                    message = (
                        f"Afbrudt efter {consecutive_failures} fejl i træk. "
                        "Kilden er sandsynligvis utilgængelig."
                    )
                    logger.error("import.aborted", extra={"import_run_id": run.id})
                    self._finish_run(run, summary, aborted_message=message)
                    return summary

        self._finish_run(run, summary)
        return summary

    # -- Pipelinen for ét dokument -----------------------------------------

    def _process_one(self, ref: DocumentRef, run_id: int, summary: ImportSummary) -> None:
        """Behandler ét kildedokument gennem hele pipelinen."""
        normalized = self._fetch(ref)

        # 1. Relevans — den automatiske motor kører ALTID, uanset om en
        #    kurateret override findes. Dens resultat er det, der lander i
        #    maritime_score/relevance_details, uændret.
        relevance = self.relevance_engine.classify(normalized)
        logger.info(
            "import.document.classified",
            extra={
                "source_id": normalized.source_id,
                "score": relevance.score,
                "maritime": relevance.is_maritime,
                "classification": relevance.classification,
            },
        )

        # 2. Kurateret override — en menneskelig rettelse af den EFFEKTIVE
        #    afgørelse for netop dette accessionsnummer. Se
        #    app.services.curation.overrides for hvorfor motorens egne tal
        #    aldrig ændres af dette opslag.
        override = self.repository.get_curated_override(normalized.source_id)
        if override is not None:
            logger.info(
                "import.document.curated_override",
                extra={
                    "source_id": normalized.source_id,
                    "decision": override.decision,
                    "source_tag": override.source_tag,
                    "automatic_score": relevance.score,
                    "automatic_classification": relevance.classification,
                },
            )

        # 3. Afvis dokumenter under lagringstærsklen — medmindre en
        #    kurateret afgørelse findes. En override, uanset retning,
        #    betyder at et menneske allerede har taget stilling til dette
        #    accessionsnummer, og afgørelsen skal kunne aflæses i databasen
        #    (se punkt 5 for hvorfor "exclude" stadig gemmer dokumentet).
        #    Databasen skal forblive en maritim samling for al ANDEN
        #    lovgivning — denne regel er uændret uden override.
        if override is None and relevance.score < self.store_min_score:
            summary.rejected += 1
            summary.outcomes.append(
                DocumentOutcome(
                    source_id=normalized.source_id, outcome=Outcome.REJECTED
                )
            )
            logger.info(
                "import.document.rejected",
                extra={
                    "source_id": normalized.source_id,
                    "score": relevance.score,
                    "threshold": self.store_min_score,
                },
            )
            return

        is_curated_exclude = override is not None and override.decision == "exclude"

        # 4. Kategorisering — sprunget over for et kurateret ekskluderet
        #    dokument. Det er allerede afgjort ikke at være maritimt; at
        #    tildele maritime kategorier ville modsige selve afgørelsen.
        if is_curated_exclude:
            categorization = CategorizationResult()
        else:
            categorization = self.categorization_engine.categorize(normalized)

        # 5. Persistering og versionering. Dokumentet gemmes/opdateres
        #    ALTID her, også ved curated exclude — ellers ville et allerede
        #    importeret dokument, der siden ekskluderes, blive stående med
        #    sin gamle (forkerte) is_maritime-værdi. repository.store()
        #    beregner den effektive is_maritime fra override.
        outcome = self.repository.store(
            normalized, relevance, categorization, import_run_id=run_id, override=override
        )

        if is_curated_exclude:
            # Effektivt afvist, selvom dokumentet er gemt (til sporbarhed).
            # Køstatus skal være REJECTED, ikke COMPLETED.
            summary.rejected += 1
            summary.outcomes.append(
                DocumentOutcome(source_id=normalized.source_id, outcome=Outcome.REJECTED)
            )
            return

        if outcome.created:
            summary.created += 1
            recorded = Outcome.CREATED
        elif outcome.content_changed or outcome.metadata_changed:
            summary.updated += 1
            recorded = Outcome.UPDATED
        else:
            summary.unchanged += 1
            recorded = Outcome.UNCHANGED

        summary.outcomes.append(
            DocumentOutcome(source_id=normalized.source_id, outcome=recorded)
        )

    def _fetch(self, ref: DocumentRef) -> NormalizedDocument:
        """Henter det fulde, normaliserede dokument fra kilden."""
        try:
            return self.client.get_document(ref.source_id)
        except SourceError:
            # Kildens egne fejltyper bæres videre uændret, så kalderen kan
            # skelne midlertidigt fra permanent (se DocumentOutcome.retryable).
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Kunne ikke hente dokument {ref.source_id}: {exc}"
            ) from exc

    def _discover(
        self, since: date | None, explicit_ids: list[str] | None
    ) -> Iterable[DocumentRef]:
        if explicit_ids is not None:
            return self.client.get_documents(explicit_ids=explicit_ids)
        if since is not None:
            return self.client.get_updated_documents(since)
        return self.client.get_documents()

    # -- Importkørslens livscyklus ------------------------------------------

    def _start_run(self, trigger: str) -> ImportRun:
        run = ImportRun(
            source=getattr(self.client, "kind", "ukendt"),
            client_kind=getattr(self.client, "kind", "ukendt"),
            trigger=trigger,
            started_at=datetime.now(timezone.utc),
            status=ImportStatus.RUNNING.value,
        )
        self.session.add(run)
        self.session.commit()
        return run

    def _record_error(self, summary: ImportSummary, source_id: str, exc: Exception) -> None:
        if len(summary.errors) < MAX_STORED_ERRORS:
            summary.errors.append(
                {
                    "source_id": source_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )

    def _finish_run(
        self,
        run: ImportRun,
        summary: ImportSummary,
        *,
        aborted_message: str | None = None,
    ) -> None:
        run.finished_at = datetime.now(timezone.utc)
        run.documents_checked = summary.checked
        run.documents_created = summary.created
        run.documents_updated = summary.updated
        run.documents_unchanged = summary.unchanged
        run.documents_rejected = summary.rejected
        run.documents_failed = summary.failed
        run.errors = summary.errors or None

        if aborted_message:
            run.status = ImportStatus.FAILED.value
            run.error_message = aborted_message
        elif summary.failed:
            run.status = ImportStatus.COMPLETED_WITH_ERRORS.value
            run.error_message = f"{summary.failed} dokument(er) kunne ikke behandles"
        else:
            run.status = ImportStatus.COMPLETED.value

        summary.status = run.status
        summary.finished_at = run.finished_at
        self.session.commit()

        logger.info(
            "import.completed",
            extra={
                "import_run_id": run.id,
                "status": run.status,
                "checked": summary.checked,
                "created_count": summary.created,
                "updated": summary.updated,
                "unchanged": summary.unchanged,
                "rejected": summary.rejected,
                "failed": summary.failed,
            },
        )

    def _fail_run(self, run: ImportRun, message: str) -> None:
        run.finished_at = datetime.now(timezone.utc)
        run.status = ImportStatus.FAILED.value
        run.error_message = message
        self.session.commit()

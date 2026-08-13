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
    SourceClient,
)

from .repository import DocumentRepository

logger = get_logger(__name__)

__all__ = ["ImportService", "ImportSummary"]

#: Maks. antal fejldetaljer der gemmes på importkørslen.
MAX_STORED_ERRORS = 50


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
    ) -> ImportSummary:
        """Kører en fuld import.

        Args:
            since: Hent kun dokumenter ændret fra denne dato.
            trigger: Hvordan importen blev startet ("manual", "api", "cli").
            limit: Behandl højst dette antal dokumenter. Bruges til test.
        """
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
            },
        )

        try:
            refs = list(self._discover(since))
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

        # 1. Relevans
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

        # 2. Afvis dokumenter under lagringstærsklen. Databasen skal
        #    forblive en maritim samling, ikke en kopi af hele lovsamlingen.
        if relevance.score < self.store_min_score:
            summary.rejected += 1
            logger.info(
                "import.document.rejected",
                extra={
                    "source_id": normalized.source_id,
                    "score": relevance.score,
                    "threshold": self.store_min_score,
                },
            )
            return

        # 3. Kategorisering — kun for dokumenter vi beholder.
        categorization: CategorizationResult = self.categorization_engine.categorize(normalized)

        # 4. Persistering og versionering
        outcome = self.repository.store(
            normalized, relevance, categorization, import_run_id=run_id
        )

        if outcome.created:
            summary.created += 1
        elif outcome.content_changed or outcome.metadata_changed:
            summary.updated += 1
        else:
            summary.unchanged += 1

    def _fetch(self, ref: DocumentRef) -> NormalizedDocument:
        """Henter det fulde, normaliserede dokument fra kilden."""
        try:
            return self.client.get_document(ref.source_id)
        except DocumentNotFoundError:
            # Kilden har annonceret dokumentet, men kan ikke levere det.
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Kunne ikke hente dokument {ref.source_id}: {exc}"
            ) from exc

    def _discover(self, since: date | None) -> Iterable[DocumentRef]:
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

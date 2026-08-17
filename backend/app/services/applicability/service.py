"""Orkestrering: udkastkørsler og vurdering af en fartøjsprofil mod databasen."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.text import fold
from app.models import (
    ApplicabilityCoverageGap,
    ApplicabilityDraftRun,
    ApplicabilityRule,
    Document,
    DocumentVersion,
    RuleReviewStatus,
)

from .drafting import build_rule_drafts
from .engine import ApplicabilityResult, evaluate_applicability
from .logic import EvalOptions
from .profile import VesselProfile
from .ranking import RankedResult, rank_results
from .rules import CoverageLevel
from .repository import load_rule_specs, persist_draft, set_review_status
from .retrieval import attach_supporting_fragments, build_retrieval_text, fetch_supporting_fragments

logger = get_logger(__name__)

__all__ = ["DraftRunSummary", "ApplicabilityService", "OpenCoverageGaps"]


class OpenCoverageGaps(RuntimeError):
    """Rejses når nogen forsøger at erklære et ufuldstændigt skop for komplet."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            f"Reglen har {count} åbne mangler i anvendelsesområdet. "
            "Løs dem, eller godkend med dækningsgrad 'partial'."
        )


@dataclass(slots=True)
class DraftRunSummary:
    run_id: int
    status: str
    scope: str
    documents_scanned: int = 0
    rules_created: int = 0
    rules_unchanged: int = 0
    documents_without_scope: int = 0
    documents_failed: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)


#: Dokumenttype → bindende virkning. Lavere tal er en stærkere retskilde.
_BINDINGNESS: tuple[tuple[str, int], ...] = (
    ("lovbekendtgoerelse", 1),
    ("lov", 1),
    ("anordning", 2),
    ("bekendtgoerelse", 2),
    ("teknisk forskrift", 3),
    ("cirkulaere", 3),
    ("meddelelse", 4),
    ("vejledning", 4),
)


def _bindingness(document_type: str | None) -> int:
    folded = fold(document_type or "")
    for needle, value in _BINDINGNESS:
        if needle in folded:
            return value
    return 2


def _status_state(status: str | None) -> str:
    """Kortlægger kildens statustekst til motorens gyldighedsbegreb.

    Kan den ikke afgøres, bliver den ``unknown`` — og motoren sender sagen til
    manuel gennemgang frem for at antage, at reglen gælder.
    """
    folded = fold(status or "")
    if not folded:
        return "unknown"
    if "gaelden" in folded:
        return "in_force"
    if "historisk" in folded or "ophaev" in folded or "bortfald" in folded:
        return "repealed"
    return "unknown"


class ApplicabilityService:
    """Bindeled mellem databasen og den deterministiske motor."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- Udkast ----------------------------------------------------------

    def run_draft_generation(
        self,
        *,
        scope: str = "maritime",
        limit: int | None = None,
        document_ids: list[int] | None = None,
        trigger: str = "cli",
    ) -> DraftRunSummary:
        """Danner regeludkast for dokumenter i basen.

        Udkast er ikke regler. De får ``review_status = draft`` og bruges ikke
        af den offentlige vurdering, før et menneske har godkendt dem.
        """
        run = ApplicabilityDraftRun(
            started_at=datetime.now(timezone.utc), status="RUNNING", scope=scope, trigger=trigger
        )
        self.session.add(run)
        self.session.flush()
        summary = DraftRunSummary(run_id=run.id, status="RUNNING", scope=scope)

        stmt = (
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
            .order_by(Document.id)
        )
        if scope == "maritime":
            stmt = stmt.where(Document.is_maritime.is_(True))
        if document_ids:
            stmt = stmt.where(Document.id.in_(document_ids))
        if limit:
            stmt = stmt.limit(limit)

        logger.info("applicability.draft.started", extra={"run_id": run.id, "scope": scope})

        for document, version in self.session.execute(stmt):
            summary.documents_scanned += 1
            try:
                drafts = build_rule_drafts(
                    document_id=document.id,
                    document_version_id=version.id,
                    content=version.content,
                    title=document.display_title or document.title,
                    authority=document.authority,
                    document_type=document.document_type,
                    in_force_from=document.effective_date or document.published_date,
                    status_state=_status_state(document.status),
                    bindingness=_bindingness(document.document_type),
                )
            except Exception as exc:  # noqa: BLE001 - én skæv tekst må ikke vælte kørslen
                summary.documents_failed += 1
                summary.errors.append({"document_id": str(document.id), "error": str(exc)})
                logger.warning(
                    "applicability.draft.document_failed",
                    extra={"document_id": document.id, "error": str(exc)},
                )
                continue

            if not drafts:
                summary.documents_without_scope += 1
                continue

            for draft in drafts:
                created = persist_draft(self.session, draft, draft_run_id=run.id)
                if created is None:
                    summary.rules_unchanged += 1
                else:
                    summary.rules_created += 1
            # Én transaktion pr. dokument: en fejl sent i kørslen må ikke koste
            # de udkast, der allerede er dannet korrekt.
            self.session.commit()

        run.finished_at = datetime.now(timezone.utc)
        run.status = "COMPLETED_WITH_ERRORS" if summary.documents_failed else "COMPLETED"
        run.documents_scanned = summary.documents_scanned
        run.rules_created = summary.rules_created
        run.rules_unchanged = summary.rules_unchanged
        run.documents_without_scope = summary.documents_without_scope
        run.documents_failed = summary.documents_failed
        if summary.errors:
            run.error_message = "; ".join(
                f"{item['document_id']}: {item['error'][:120]}" for item in summary.errors[:5]
            )
        summary.status = run.status
        self.session.commit()

        logger.info(
            "applicability.draft.completed",
            extra={
                "run_id": run.id,
                "scanned": summary.documents_scanned,
                "created": summary.rules_created,
                "unchanged": summary.rules_unchanged,
                "without_scope": summary.documents_without_scope,
                "failed": summary.documents_failed,
            },
        )
        return summary

    # -- Vurdering -------------------------------------------------------

    def evaluate_profile(
        self,
        profile: VesselProfile,
        *,
        assessment_date: date | None = None,
        status_mode: str = "current",
        treat_estimated_as_unknown: bool = False,
        include_unapproved: bool = False,
        document_ids: list[int] | None = None,
        rule_ids: list[int] | None = None,
        with_fragments: bool = True,
        fragment_limit: int = 3,
        drop_non_applicable: bool = False,
    ) -> list[RankedResult]:
        """Vurderer profilen mod de godkendte regler og rangerer svaret.

        ``include_unapproved`` er kun til administrativ forhåndsvisning. Den
        offentlige rute sætter den aldrig.
        """
        options = EvalOptions(
            assessment_date=assessment_date or profile.assessment_date or date.today(),
            status_mode=status_mode,
            treat_estimated_as_unknown=treat_estimated_as_unknown,
        )
        specs = load_rule_specs(
            self.session,
            review_status=None if include_unapproved else RuleReviewStatus.APPROVED.value,
            document_ids=document_ids,
            rule_ids=rule_ids,
        )

        results: list[ApplicabilityResult] = [
            evaluate_applicability(profile, spec, options) for spec in specs
        ]

        if with_fragments and results:
            # Understøttende tekst hentes FØRST efter afgørelserne er truffet.
            # Rækkefølgen er ikke tilfældig — den er hele garantien.
            interesting = [
                r.document_id
                for r in results
                if r.verdict.value in ("APPLIES", "POSSIBLY_APPLIES", "NEEDS_MANUAL_REVIEW")
            ]
            fragments = fetch_supporting_fragments(
                self.session,
                document_ids=sorted(set(interesting)),
                query_text=build_retrieval_text(profile),
                limit=fragment_limit,
            )
            for result in results:
                attach_supporting_fragments(result, fragments.get(result.document_id, []))

        return rank_results(results, drop_non_applicable=drop_non_applicable)

    # -- Gennemgang ------------------------------------------------------

    def decide_review(
        self,
        rule: ApplicabilityRule,
        status: RuleReviewStatus,
        *,
        actor: str | None = None,
        note: str | None = None,
        coverage_level: CoverageLevel | None = None,
    ) -> ApplicabilityRule:
        """Gør et udkast til en regel — eller lader være.

        ``coverage_level = complete`` kræver, at der ikke står åbne mangler
        tilbage. Ellers ville et menneske kunne godkende en modellering,
        systemet selv har erklæret ufuldstændig, og hele dækningsregnskabet
        ville være til pynt. Reglen står her frem for i ruten, så CLI og API
        håndhæver den ens.
        """
        if coverage_level is CoverageLevel.COMPLETE:
            open_gaps = self.session.scalar(
                select(func.count(ApplicabilityCoverageGap.id)).where(
                    ApplicabilityCoverageGap.rule_id == rule.id,
                    ApplicabilityCoverageGap.resolved.is_(False),
                )
            )
            if open_gaps:
                raise OpenCoverageGaps(open_gaps)

        set_review_status(
            self.session,
            rule,
            status,
            actor=actor,
            note=note,
            coverage_level=coverage_level,
        )
        self.session.commit()
        logger.info(
            "applicability.review.decided",
            extra={"rule_id": rule.id, "status": status.value, "actor": actor},
        )
        return rule

    # -- Nøgletal --------------------------------------------------------

    def review_stats(self) -> dict[str, int]:
        """Køens tilstand: hvor mange udkast venter på at blive gennemgået."""
        rows = self.session.execute(
            select(ApplicabilityRule.review_status, func.count(ApplicabilityRule.id)).group_by(
                ApplicabilityRule.review_status
            )
        ).all()
        stats = {status: 0 for status in RuleReviewStatus.values()}
        for status, count in rows:
            stats[status] = count
        stats["total"] = sum(stats[s] for s in RuleReviewStatus.values())
        return stats

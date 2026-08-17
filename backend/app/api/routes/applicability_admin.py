"""Drift og gennemgang af regeludkast.

HELE denne router kræver administratortoken — se `app/api/routes/__init__.py`.
Det er en sikkerhedsgrænse, ikke kun en filopdeling: her ligger den handling,
der gør et maskinelt udkast til en regel, systemet lægger til grund for et svar.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, Pagination
from app.api.routes.applicability import rule_detail_out
from app.core.logging import get_logger
from app.models import (
    ApplicabilityCondition,
    ApplicabilityCoverageGap,
    ApplicabilityDraftRun,
    ApplicabilityRule,
    Document,
    RuleReviewStatus,
)
from app.schemas import Page
from app.schemas.applicability import (
    ApplicabilityResponse,
    DraftRunOut,
    EvaluationRequest,
    ReviewDecisionIn,
    ReviewQueueItemOut,
    RuleDetailOut,
    to_rule_card,
)
from app.services.applicability import ApplicabilityService, CoverageLevel, Verdict
from app.services.applicability.service import OpenCoverageGaps

logger = get_logger(__name__)

router = APIRouter(prefix="/applicability", tags=["anvendelighed-drift"])


# ---------------------------------------------------------------------------
# Udkastkørsler
# ---------------------------------------------------------------------------


@router.post(
    "/drafts/run",
    response_model=DraftRunOut,
    summary="Dan regeludkast ud fra dokumenternes § 1 / anvendelsesområde",
)
def run_drafts(
    session: DbSession,
    scope: Annotated[
        str,
        Query(
            pattern="^(maritime|all)$",
            description="'maritime' kører kun på dokumenter over relevanstærsklen.",
        ),
    ] = "maritime",
    limit: Annotated[int | None, Query(ge=1, le=5000)] = None,
) -> DraftRunOut:
    """Udkast er ikke regler.

    Alt der dannes her, får ``review_status = draft`` og bruges ikke af den
    offentlige vurdering. Dækningsgraden kan ikke blive ``complete``, uanset
    hvor godt udtrækket ser ud.
    """
    service = ApplicabilityService(session)
    summary = service.run_draft_generation(scope=scope, limit=limit, trigger="api")
    run = session.get(ApplicabilityDraftRun, summary.run_id)
    if run is None:  # pragma: no cover - kan ikke ske, kørslen er lige skrevet
        raise HTTPException(status_code=500, detail="Kørslen kunne ikke læses tilbage.")
    return DraftRunOut.model_validate(run)


@router.get(
    "/drafts/runs",
    response_model=Page[DraftRunOut],
    summary="Historik over udkastkørsler",
)
def list_draft_runs(session: DbSession, pagination: Pagination) -> Page[DraftRunOut]:
    page, page_size = pagination
    total = session.scalar(select(func.count(ApplicabilityDraftRun.id))) or 0
    rows = session.scalars(
        select(ApplicabilityDraftRun)
        .order_by(ApplicabilityDraftRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(
        items=[DraftRunOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


# ---------------------------------------------------------------------------
# Gennemgangskøen
# ---------------------------------------------------------------------------


@router.get(
    "/review",
    response_model=Page[ReviewQueueItemOut],
    summary="Regeludkast, der venter på gennemgang",
)
def review_queue(
    session: DbSession,
    pagination: Pagination,
    review_status: Annotated[str, Query(pattern="^(draft|approved|rejected|needs_changes)$")] = "draft",
) -> Page[ReviewQueueItemOut]:
    """Køen sorteres med de bedst udtrukne udkast først.

    Et udkast med mange betingelser og få mangler er hurtigst at tage stilling
    til, og et menneskes tid er den knappe ressource i hele denne pipeline.
    """
    page, page_size = pagination

    condition_count = (
        select(func.count(ApplicabilityCondition.id))
        .where(
            ApplicabilityCondition.rule_id == ApplicabilityRule.id,
            ApplicabilityCondition.node_type == "atom",
        )
        .scalar_subquery()
    )
    gap_count = (
        select(func.count(ApplicabilityCoverageGap.id))
        .where(
            ApplicabilityCoverageGap.rule_id == ApplicabilityRule.id,
            ApplicabilityCoverageGap.resolved.is_(False),
        )
        .scalar_subquery()
    )
    low_confidence = (
        select(func.count(ApplicabilityCondition.id))
        .where(
            ApplicabilityCondition.rule_id == ApplicabilityRule.id,
            ApplicabilityCondition.draft_confidence == "low",
        )
        .scalar_subquery()
    )

    base = select(ApplicabilityRule).where(ApplicabilityRule.review_status == review_status)
    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = session.execute(
        select(
            ApplicabilityRule,
            Document,
            condition_count.label("conditions"),
            gap_count.label("gaps"),
            low_confidence.label("low"),
        )
        .join(Document, Document.id == ApplicabilityRule.document_id)
        .where(ApplicabilityRule.review_status == review_status)
        .options(selectinload(ApplicabilityRule.citations))
        .order_by(condition_count.desc(), gap_count.asc(), ApplicabilityRule.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [
        ReviewQueueItemOut(
            rule_id=rule.id,
            document_id=rule.document_id,
            document_title=document.display_title or document.title,
            rule_ref=rule.rule_ref,
            review_status=rule.review_status,
            coverage_level=rule.coverage_level,
            coverage_gaps=gaps,
            condition_count=conditions,
            citation_count=len(rule.citations),
            low_confidence_conditions=low,
            created_at=rule.created_at,
        )
        for rule, document, conditions, gaps, low in rows
    ]
    return Page(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get(
    "/review/{rule_id}",
    response_model=RuleDetailOut,
    summary="Se et udkast med citater, betingelser og mangler",
    responses={404: {"description": "Reglen findes ikke."}},
)
def review_detail(rule_id: Annotated[int, Path(ge=1)], session: DbSession) -> RuleDetailOut:
    row = session.execute(
        select(ApplicabilityRule, Document)
        .join(Document, Document.id == ApplicabilityRule.document_id)
        .where(ApplicabilityRule.id == rule_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reglen findes ikke.")
    rule, document = row
    return rule_detail_out(rule, document)


@router.post(
    "/review/{rule_id}/decision",
    response_model=RuleDetailOut,
    summary="Godkend, afvis eller genåbn et regeludkast",
    responses={
        400: {"description": "Dækningsgraden 'complete' kræver, at der ikke er åbne mangler."},
        404: {"description": "Reglen findes ikke."},
    },
)
def review_decision(
    rule_id: Annotated[int, Path(ge=1)],
    payload: ReviewDecisionIn,
    session: DbSession,
) -> RuleDetailOut:
    """Den handling, der gør et udkast til en regel.

    ``coverage_level = 'complete'`` kan kun sættes her, og kun når der ikke
    står åbne mangler tilbage. Ellers ville et menneske kunne godkende en
    modellering, systemet selv har erklæret ufuldstændig — og det er præcis
    den fejl, hele dækningsregnskabet findes for at forhindre.
    """
    rule = session.get(ApplicabilityRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reglen findes ikke.")

    coverage = CoverageLevel(payload.coverage_level) if payload.coverage_level else None
    try:
        ApplicabilityService(session).decide_review(
            rule,
            RuleReviewStatus(payload.status),
            actor=payload.actor,
            note=payload.note,
            coverage_level=coverage,
        )
    except OpenCoverageGaps as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    document = session.get(Document, rule.document_id)
    logger.info(
        "applicability.review.decided",
        extra={
            "rule_id": rule.id,
            "status": payload.status,
            "actor": payload.actor,
            "coverage_level": rule.coverage_level,
        },
    )
    return rule_detail_out(rule, document)


@router.post(
    "/evaluate/preview",
    response_model=ApplicabilityResponse,
    summary="Forhåndsvis en vurdering med udkast inkluderet",
)
def evaluate_preview(payload: EvaluationRequest, session: DbSession) -> ApplicabilityResponse:
    """Samme motor, men også ikke-godkendte regler.

    Beregnet til at afprøve et udkast før godkendelse. Svaret bærer en
    advarsel, så det ikke forveksles med den offentlige vurdering.
    """
    from datetime import date as _date

    assessment_date = payload.assessment_date or _date.today()
    service = ApplicabilityService(session)
    profile = payload.profile.to_domain(assessment_date)

    ranked = service.evaluate_profile(
        profile,
        assessment_date=assessment_date,
        status_mode=payload.status_mode,
        treat_estimated_as_unknown=payload.treat_estimated_as_unknown,
        include_unapproved=True,
        with_fragments=payload.with_supporting_text,
        drop_non_applicable=not payload.include_non_applicable,
    )
    counts = {verdict.value: 0 for verdict in Verdict}
    for entry in ranked:
        counts[entry.result.verdict.value] += 1

    return ApplicabilityResponse(
        profile_id=profile.profile_id,
        assessment_date=assessment_date,
        status_mode=payload.status_mode,
        counts=counts,
        results=[to_rule_card(entry) for entry in ranked],
        rules_evaluated=len(ranked),
        unapproved_notice=(
            "ADVARSEL: dette svar indeholder regeludkast, der endnu ikke er gennemgået "
            "af et menneske. Udkast må ikke lægges til grund for en juridisk vurdering."
        ),
        engine={
            "version": ranked[0].result.engine_version if ranked else "1.0.0",
            "deterministic": True,
            "used_language_model": False,
            "inputs_hash": ranked[0].result.inputs_hash if ranked else None,
        },
    )

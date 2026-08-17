"""Offentlige ruter for anvendelighedsvurdering.

Denne router ser **kun godkendte regler**. Udkast fra skopparseren findes i
databasen, men kommer aldrig ud herfra — se `applicability_admin.py` for
gennemgangskøen.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.logging import get_logger
from app.models import ApplicabilityRule, Document, RuleReviewStatus
from app.schemas.applicability import (
    ApplicabilityResponse,
    CitationOut,
    CoverageGapOut,
    EvaluationRequest,
    FieldSpecOut,
    RuleConditionOut,
    RuleDetailOut,
    field_registry_out,
    to_rule_card,
)
from app.services.applicability import ApplicabilityService, Verdict

logger = get_logger(__name__)

router = APIRouter(prefix="/applicability", tags=["anvendelighed"])


@router.get(
    "/fields",
    response_model=list[FieldSpecOut],
    summary="Felter en fartøjsprofil kan indeholde",
)
def list_fields() -> list[FieldSpecOut]:
    """Feltregisteret — nok til at bygge inddateringsformularen dynamisk.

    Tilføjes et felt i motoren, dukker det op her uden en frontend-udrulning.
    """
    return field_registry_out()


@router.post(
    "/evaluate",
    response_model=ApplicabilityResponse,
    summary="Vurder en fartøjsprofil mod de godkendte regler",
)
def evaluate(payload: EvaluationRequest, session: DbSession) -> ApplicabilityResponse:
    """Afsiger APPLIES / POSSIBLY_APPLIES / DOES_NOT_APPLY / NEEDS_MANUAL_REVIEW.

    Deterministisk: ingen sprogmodel indgår i afgørelsen, og hvert svar bærer
    en inputhash, så to kørsler kan sammenlignes. Vektorsøgningen bruges først
    **efter** afgørelsen, til at finde den lovtekst et menneske bør læse.
    """
    assessment_date = payload.assessment_date or date.today()
    service = ApplicabilityService(session)
    profile = payload.profile.to_domain(assessment_date)

    ranked = service.evaluate_profile(
        profile,
        assessment_date=assessment_date,
        status_mode=payload.status_mode,
        treat_estimated_as_unknown=payload.treat_estimated_as_unknown,
        include_unapproved=False,
        with_fragments=payload.with_supporting_text,
        drop_non_applicable=not payload.include_non_applicable,
    )

    cards = [to_rule_card(entry) for entry in ranked]
    counts = {verdict.value: 0 for verdict in Verdict}
    for entry in ranked:
        counts[entry.result.verdict.value] += 1

    logger.info(
        "applicability.evaluated",
        extra={
            "profile_id": profile.profile_id,
            "vessel_type": profile.vessel_type.value,
            "rules": len(ranked),
            "applies": counts[Verdict.APPLIES.value],
            "review": counts[Verdict.NEEDS_MANUAL_REVIEW.value],
        },
    )

    return ApplicabilityResponse(
        profile_id=profile.profile_id,
        assessment_date=assessment_date,
        status_mode=payload.status_mode,
        counts=counts,
        results=cards,
        rules_evaluated=len(ranked),
        engine={
            "version": ranked[0].result.engine_version if ranked else "1.0.0",
            "deterministic": True,
            "used_language_model": False,
            "inputs_hash": ranked[0].result.inputs_hash if ranked else None,
        },
    )


@router.get(
    "/rules/{rule_id}",
    response_model=RuleDetailOut,
    summary="Se en godkendt regels anvendelsesområde",
    responses={404: {"description": "Reglen findes ikke eller er ikke godkendt."}},
)
def get_rule(
    rule_id: Annotated[int, Path(ge=1)],
    session: DbSession,
) -> RuleDetailOut:
    """Kun godkendte regler er offentlige.

    Et udkast er ikke en regel, og et 404 er et ærligere svar end at udlevere
    noget, ingen har taget stilling til.
    """
    row = session.execute(
        select(ApplicabilityRule, Document)
        .join(Document, Document.id == ApplicabilityRule.document_id)
        .where(
            ApplicabilityRule.id == rule_id,
            ApplicabilityRule.review_status == RuleReviewStatus.APPROVED.value,
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reglen findes ikke eller er ikke godkendt til brug.",
        )
    rule, document = row
    return rule_detail_out(rule, document)


def rule_detail_out(rule: ApplicabilityRule, document: Document) -> RuleDetailOut:
    """Fælles serialisering — bruges både offentligt og i gennemgangskøen."""
    return RuleDetailOut(
        rule_id=rule.id,
        document_id=rule.document_id,
        document_title=document.display_title or document.title,
        document_version_id=rule.document_version_id,
        rule_ref=rule.rule_ref,
        title=rule.title,
        authority=rule.authority,
        document_type=rule.document_type,
        status_state=rule.status_state,
        in_force_from=rule.in_force_from,
        in_force_to=rule.in_force_to,
        coverage_level=rule.coverage_level,
        review_status=rule.review_status,
        reviewed_by=rule.reviewed_by,
        reviewed_at=rule.reviewed_at,
        origin=rule.origin,
        bindingness=rule.bindingness,
        citations=[
            CitationOut(
                citation_key=citation.citation_key,
                ref=citation.ref,
                text=citation.text,
                kind=citation.kind,
                char_start=citation.char_start,
                char_end=citation.char_end,
                document_version_id=citation.document_version_id,
                text_hash=citation.text_hash,
            )
            for citation in sorted(rule.citations, key=lambda c: c.citation_key)
        ],
        conditions=[
            RuleConditionOut(
                id=condition.id,
                clause_kind=condition.clause_kind,
                clause_id=condition.clause_id,
                parent_id=condition.parent_id,
                node_type=condition.node_type,
                field=condition.field_name,
                op=condition.op,
                value=condition.value_json,
                citation_key=condition.citation_key,
                unknown_policy=condition.unknown_policy,
                tolerance=condition.tolerance,
                draft_confidence=condition.draft_confidence,
                note=condition.note,
            )
            for condition in sorted(rule.conditions, key=lambda c: (c.clause_kind, c.position, c.id))
        ],
        coverage_gaps=[
            CoverageGapOut(
                citation_key=gap.citation_key, reason=gap.reason, resolved=bool(gap.resolved)
            )
            for gap in rule.coverage_gaps
        ],
        review_events=[
            {
                "event_type": event.event_type,
                "actor": event.actor,
                "note": event.note,
                "previous_status": event.previous_status,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in rule.review_events
        ],
    )

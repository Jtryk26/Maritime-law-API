"""Vessel Applicability 2.0 — ruter under ``/api/v1/applicability``.

To routere, af samme grund som i `applicability.py` / `applicability_admin.py`:

* ``router`` er offentlig og kan kun **læse** godkendte regler.
* ``admin_router`` udkaster og godkender. Den får administratorkravet på
  routerniveau i `app/api/routes/__init__.py` og findes slet ikke, når
  ``ENABLE_ADMIN_API=false``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from app.api.deps import DbSession
from app.core.logging import get_logger
from app.schemas.vessel_applicability import (
    ApprovedRuleOut,
    ApprovedRuleStatusOut,
    ApproveRuleIn,
    DraftRuleIn,
    StoredRuleOut,
)
from app.services.vessel_applicability import (
    EvaluationReport,
    InvalidRule,
    RuleConflict,
    RuleNotFound,
    Verdict,
    VesselApplicabilityService,
    VesselProfile,
)
from app.services.vessel_applicability.service import integrity_problem

logger = get_logger(__name__)

router = APIRouter(prefix="/applicability", tags=["anvendelighed-v2"])
admin_router = APIRouter(prefix="/applicability", tags=["anvendelighed-v2-drift"])

RuleIdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]


@router.post(
    "/evaluate",
    response_model=EvaluationReport,
    summary="Vurder en fartøjsprofil mod alle godkendte regler",
)
def evaluate(profile: VesselProfile, session: DbSession) -> EvaluationReport:
    """Deterministisk: ren boolsk evaluering i Python, ingen sprogmodel.

    Hver regel får ét af fire udfald med fuldt spor — beslutningstrin,
    betingelser med faktiske værdier og den præcise lovhenvisning — og
    resultaterne grupperes efter ``topic_category``. Regler, der ikke er
    godkendt, indgår ikke, og fravær af en regel betyder ikke, at intet
    gælder (se ``coverage_notice``).
    """
    report = VesselApplicabilityService(session).evaluate(profile)
    logger.info(
        "vessel_applicability.evaluated",
        extra={
            "profile_hash": report.profile_hash,
            "rules": report.rules_evaluated,
            "applies": report.counts[Verdict.APPLIES.value],
            "review": report.counts[Verdict.NEEDS_MANUAL_REVIEW.value],
            "cached": report.cached,
        },
    )
    return report


@router.get(
    "/rules/approved",
    response_model=list[ApprovedRuleStatusOut],
    summary="Godkendte regler og deres integritet",
)
def list_approved(session: DbSession) -> list[ApprovedRuleStatusOut]:
    """Det regelsæt, ``/evaluate`` faktisk bruger — til kontrol før udrulning.

    Offentlig og skrivebeskyttet, så den også virker i produktion, hvor
    administratorruterne er slået fra. Kun godkendte regler vises.
    """
    out = []
    for row in VesselApplicabilityService(session).approved_rules():
        problem = integrity_problem(row)
        out.append(ApprovedRuleStatusOut(
            rule_id=row.rule_id,
            status=row.status,
            citation=row.citation,
            legal_source_id=row.legal_source_id,
            topic_category=row.topic_category,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            approved_content_hash=row.approved_content_hash,
            source_text_hash=row.source_text_hash,
            integrity_verified=problem is None,
            integrity_problem=problem,
        ))
    return out


@admin_router.post(
    "/rules/draft",
    response_model=StoredRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Gem en skrabet bestemmelse som regeludkast",
    responses={
        409: {"description": "Reglen er allerede godkendt eller udfaset."},
        422: {"description": "Reglen kan ikke evalueres (ukendt felt, operator eller værdi)."},
    },
)
def create_draft(payload: DraftRuleIn, session: DbSession) -> StoredRuleOut:
    """Gemmes altid med ``status = DRAFT``, uanset hvad der sendes.

    Et eksisterende udkast med samme ``rule_id`` erstattes; en godkendt
    regel kan ikke overskrives. Svaret bærer ``content_hash``, som
    anmelderen kan sende med godkendelsen.
    """
    try:
        row = VesselApplicabilityService(session).save_draft(
            payload.rule, source_text=payload.source_text, drafted_by=payload.drafted_by
        )
    except InvalidRule as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Reglen kan ikke evalueres.", "errors": exc.errors},
        ) from exc
    except RuleConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return StoredRuleOut.from_row(row)


@admin_router.post(
    "/rules/{rule_id}/approve",
    response_model=ApprovedRuleOut,
    summary="Godkend et regeludkast",
    responses={
        404: {"description": "Reglen findes ikke."},
        409: {"description": "Reglen er ikke et udkast, er ændret, eller anmelder = udkaster."},
        422: {"description": "Udkastet kan ikke evalueres."},
    },
)
def approve_rule(
    rule_id: RuleIdPath, payload: ApproveRuleIn, session: DbSession
) -> ApprovedRuleOut:
    """Gør et udkast til en regel og kasserer berørte cachede vurderinger."""
    try:
        outcome = VesselApplicabilityService(session).approve(
            rule_id,
            reviewer_id=payload.reviewer_id,
            signature=payload.signature,
            expected_content_hash=payload.content_hash,
        )
    except RuleNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reglen findes ikke."
        ) from exc
    except InvalidRule as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Udkastet kan ikke evalueres.", "errors": exc.errors},
        ) from exc
    except RuleConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ApprovedRuleOut(
        rule=StoredRuleOut.from_row(outcome.row),
        invalidated_evaluations=outcome.invalidated_evaluations,
    )

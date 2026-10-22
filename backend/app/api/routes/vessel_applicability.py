"""Vessel Applicability 2.0 — ruter under ``/api/v1/applicability``.

Kun offentlige, læsende ruter. Reglernes livscyklus — udkast, godkendelse,
udfasning og integritetskontrol — findes udelukkende som
``python -m app.cli vessel-rules`` i drift-containeren, som forbinder som
databaseejer. Den offentlige backend forbinder som en ren læserolle og
kunne ikke skrive en regel, selv hvis en rute bad om det.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession
from app.core.logging import get_logger
from app.schemas.vessel_applicability import ApprovedRuleStatusOut
from app.services.vessel_applicability import (
    EvaluationReport,
    Verdict,
    VesselApplicabilityService,
    VesselProfile,
)
from app.services.vessel_applicability.service import integrity_problem

logger = get_logger(__name__)

router = APIRouter(prefix="/applicability", tags=["anvendelighed-v2"])


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

"""Vessel Applicability 2.0 — flade, håndskrevne anvendelighedsregler.

Lever ved siden af den første anvendelighedsmotor (`app.services.applicability`)
uden at dele tabeller eller afgørelser med den. Hvor den første modellerer
betingelsestræer udtrukket af paragraftekst, tager denne flade betingelser,
som en faglig anmelder har skrevet og godkendt.

Lagdeling::

    models    domænets typer — profil, regel, resultat
    engine    den rene, deterministiske afgørelse
    cache     færdige rapporter, nøglet på regelsættets fingeraftryk
    service   database, udkast, godkendelse og orkestrering

Ingen sprogmodel indgår nogen steder i beslutningsvejen.
"""

from .cache import EvaluationCache, evaluation_cache
from .engine import ENGINE_VERSION, evaluate_rule, evaluate_rules, supplied_fields
from .models import (
    ApplicabilityRule,
    ConditionOutcome,
    EvaluationCondition,
    EvaluationReport,
    Operator,
    RuleEvaluationResult,
    RuleStatus,
    Verdict,
    VesselProfile,
)
from .service import (
    COVERAGE_NOTICE,
    ApprovalOutcome,
    InvalidRule,
    RuleConflict,
    RuleNotFound,
    SeparationOfDutiesViolation,
    VesselApplicabilityService,
    canonicalize_legal_text,
    content_hash,
    integrity_problem,
    profile_hash,
)

__all__ = [
    "COVERAGE_NOTICE",
    "ENGINE_VERSION",
    "ApplicabilityRule",
    "ApprovalOutcome",
    "ConditionOutcome",
    "EvaluationCache",
    "EvaluationCondition",
    "EvaluationReport",
    "InvalidRule",
    "Operator",
    "RuleConflict",
    "RuleEvaluationResult",
    "RuleNotFound",
    "RuleStatus",
    "SeparationOfDutiesViolation",
    "Verdict",
    "VesselApplicabilityService",
    "VesselProfile",
    "canonicalize_legal_text",
    "content_hash",
    "evaluate_rule",
    "evaluate_rules",
    "evaluation_cache",
    "integrity_problem",
    "profile_hash",
    "supplied_fields",
]

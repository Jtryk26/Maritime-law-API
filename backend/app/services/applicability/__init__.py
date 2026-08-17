"""Deterministisk anvendelighedsmotor for maritim lovgivning.

Givet en **fartøjsprofil** og en **regel** afsiger motoren ét af fire udfald::

    APPLIES               gælder
    POSSIBLY_APPLIES      gælder muligvis
    DOES_NOT_APPLY        gælder ikke
    NEEDS_MANUAL_REVIEW   kræver manuel vurdering

Beslutningsvejen har seks faste porte — gyldighed, jurisdiktion, struktureret
metadata, tærskelværdier, undtagelser, dækning — og indeholder **ingen
sprogmodel**, intet netværk og ingen tilfældighed. Hver delafgørelse peger på
ordret skoptekst, og ``inputs_hash`` gør afgørelsen efterprøvelig.

Lagdeling::

    profile / derive / fields / logic / rules   domænets sprog
    engine / explain / ranking                  afgørelsen
    drafting                                    lovtekst → udkast til regler
    repository                                  database ↔ domæne
    retrieval                                   understøttende tekst, uden for beslutningen
    service                                     orkestrering
"""

from .derive import DerivedFacts, derive_facts
from .drafting import RuleDraft, build_rule_drafts
from .engine import (
    ENGINE_VERSION,
    ApplicabilityResult,
    DecisionStep,
    ManualReviewReason,
    SupportingFragment,
    Verdict,
    compute_confidence,
    decide_verdict,
    evaluate_applicability,
    evaluate_rules,
)
from .explain import Explanation, explain_applicability
from .fields import FIELD_REGISTRY, DataType, Gate, get_field_spec
from .logic import Atom, AllOf, Always, AnyOf, Comparator, EvalOptions, NotNode, UnknownPolicy
from .profile import (
    Dimensions,
    Jurisdiction,
    Lifecycle,
    Measured,
    OperationType,
    Persons,
    Tri,
    ValueSource,
    VesselProfile,
    VesselType,
)
from .ranking import RankedResult, group_by_verdict, rank_results
from .repository import load_rule_specs, persist_draft, set_review_status
from .retrieval import attach_supporting_fragments, build_retrieval_text, fetch_supporting_fragments
from .rules import (
    ApplicabilityRuleSpec,
    CitationKind,
    CoverageLevel,
    DiscretionClause,
    DiscretionEffect,
    ExclusionClause,
    ReviewStatus,
    RuleJurisdiction,
    RuleState,
    RuleStatus,
    ScopeCitation,
    ScopeCoverage,
)
from .service import ApplicabilityService, DraftRunSummary

__all__ = [
    "ENGINE_VERSION",
    "AllOf",
    "Always",
    "AnyOf",
    "ApplicabilityResult",
    "ApplicabilityRuleSpec",
    "ApplicabilityService",
    "Atom",
    "CitationKind",
    "Comparator",
    "CoverageLevel",
    "DataType",
    "DecisionStep",
    "DerivedFacts",
    "Dimensions",
    "DiscretionClause",
    "DiscretionEffect",
    "DraftRunSummary",
    "EvalOptions",
    "ExclusionClause",
    "Explanation",
    "FIELD_REGISTRY",
    "Gate",
    "Jurisdiction",
    "Lifecycle",
    "ManualReviewReason",
    "Measured",
    "NotNode",
    "OperationType",
    "Persons",
    "RankedResult",
    "ReviewStatus",
    "RuleDraft",
    "RuleJurisdiction",
    "RuleState",
    "RuleStatus",
    "ScopeCitation",
    "ScopeCoverage",
    "SupportingFragment",
    "Tri",
    "UnknownPolicy",
    "ValueSource",
    "Verdict",
    "VesselProfile",
    "VesselType",
    "attach_supporting_fragments",
    "build_retrieval_text",
    "build_rule_drafts",
    "compute_confidence",
    "decide_verdict",
    "derive_facts",
    "evaluate_applicability",
    "evaluate_rules",
    "explain_applicability",
    "fetch_supporting_fragments",
    "get_field_spec",
    "group_by_verdict",
    "load_rule_specs",
    "persist_draft",
    "rank_results",
    "set_review_status",
]

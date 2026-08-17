"""Regelmodellen, som motoren ser den.

Dette er domænelaget. Det ved intet om SQLAlchemy; :mod:`repository` oversætter
mellem databasen og disse genstande. Motoren kan derfor køres på en regel, der
kun findes i hukommelsen — hvilket er præcis det, en anmelder skal kunne, når
et udkast skal afprøves før godkendelse.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date

from .logic import ConditionNode

__all__ = [
    "CitationKind",
    "CoverageLevel",
    "ReviewStatus",
    "RuleState",
    "DiscretionEffect",
    "ScopeCitation",
    "RuleStatus",
    "RuleJurisdiction",
    "ExclusionClause",
    "DiscretionClause",
    "CoverageGap",
    "ScopeCoverage",
    "ApplicabilityRuleSpec",
]


class CitationKind(str, enum.Enum):
    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"
    DEFINITION = "definition"
    DISCRETION = "discretion"
    STATUS = "status"
    JURISDICTION = "jurisdiction"
    UNMODELLED = "unmodelled"


class CoverageLevel(str, enum.Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNPARSED = "unparsed"


class ReviewStatus(str, enum.Enum):
    """Et udkast er ikke en regel, før et menneske har sagt god for det."""

    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


class RuleState(str, enum.Enum):
    IN_FORCE = "in_force"
    NOT_YET_IN_FORCE = "not_yet_in_force"
    REPEALED = "repealed"
    UNKNOWN = "unknown"


class DiscretionEffect(str, enum.Enum):
    MAY_EXTEND = "may_extend"
    MAY_EXEMPT = "may_exempt"
    MAY_MODIFY = "may_modify"


@dataclass(frozen=True, slots=True)
class ScopeCitation:
    """Ordret skoptekst. Gengives uændret — motoren omskriver aldrig lovtekst."""

    key: str
    ref: str
    text: str
    kind: CitationKind = CitationKind.INCLUSION
    char_start: int | None = None
    char_end: int | None = None
    document_version_id: int | None = None
    source_url: str | None = None
    text_hash: str | None = None


@dataclass(frozen=True, slots=True)
class RuleStatus:
    state: RuleState = RuleState.UNKNOWN
    in_force_from: date | None = None
    in_force_to: date | None = None
    superseded_by_rule_id: int | None = None
    citation_key: str | None = None


@dataclass(frozen=True, slots=True)
class RuleJurisdiction:
    #: ISO-lande, eller ["*"] for enhver flagstat.
    flag_states: tuple[str, ...] = ("*",)
    #: Farvands-/områdekoder, eller ["*"].
    operating_areas: tuple[str, ...] = ("*",)
    #: Om reglen også rammer udenlandske skibe i dansk havn.
    port_state_applies: bool = False
    citation_key: str | None = None


@dataclass(slots=True)
class ExclusionClause:
    clause_id: str
    condition: ConditionNode
    citation_key: str
    label_da: str | None = None


@dataclass(slots=True)
class DiscretionClause:
    clause_id: str
    authority: str
    effect: DiscretionEffect
    citation_key: str
    condition: ConditionNode | None = None
    label_da: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageGap:
    citation_key: str | None
    reason: str


@dataclass(slots=True)
class ScopeCoverage:
    """Hvor meget af det faktiske anvendelsesområde der er modelleret som data.

    Motorens ærlighedsventil: en regel med uafklarede led kan ikke give et rent
    ``APPLIES``. ``COMPLETE`` kan kun sættes af et menneske ved godkendelse.
    """

    level: CoverageLevel = CoverageLevel.UNPARSED
    gaps: list[CoverageGap] = field(default_factory=list)
    reviewed_by: str | None = None
    reviewed_at: date | None = None


@dataclass(slots=True)
class ApplicabilityRuleSpec:
    """En regel klar til evaluering."""

    rule_id: int | None
    document_id: int
    rule_ref: str
    title: str
    authority: str | None = None
    document_type: str | None = None
    document_version_id: int | None = None
    source_url: str | None = None
    is_synthetic: bool = False

    status: RuleStatus = field(default_factory=RuleStatus)
    jurisdiction: RuleJurisdiction = field(default_factory=RuleJurisdiction)

    inclusion: ConditionNode | None = None
    exclusions: list[ExclusionClause] = field(default_factory=list)
    discretion: list[DiscretionClause] = field(default_factory=list)

    citations: dict[str, ScopeCitation] = field(default_factory=dict)
    coverage: ScopeCoverage = field(default_factory=ScopeCoverage)

    review_status: ReviewStatus = ReviewStatus.DRAFT
    bindingness: int = 2
    speciality_boost: int = 0

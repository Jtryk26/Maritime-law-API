"""Domænets sprog for Vessel Applicability 2.0.

Tre modeller bærer hele kontrakten:

``VesselProfile``
    Skibet, som brugeren beskriver det. Flad struktur — hvert felt kan
    adresseres direkte af en betingelse.

``ApplicabilityRule``
    En bestemmelses anvendelsesområde som data: inklusions- og
    undtagelsesbetingelser, gyldighedsperiode og kølstrækningsvindue.

``RuleEvaluationResult``
    Afgørelsen med fuldt revisionsspor: hver betingelse, den faktiske værdi
    den blev holdt op imod, og den præcise lovhenvisning.

Ingen af modellerne kender databasen. Motoren (`engine.py`) arbejder
udelukkende på disse typer, så den kan afprøves og efterprøves uden I/O.
"""

from __future__ import annotations

import enum
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "PROFILE_FIELD_KINDS",
    "ApplicabilityRule",
    "ConditionOutcome",
    "ConditionTrace",
    "EvaluationCondition",
    "EvaluationReport",
    "FieldKind",
    "Operator",
    "RuleEvaluationResult",
    "RuleStatus",
    "TopicGroup",
    "TraceStep",
    "Verdict",
    "VesselProfile",
]


class Verdict(str, enum.Enum):
    APPLIES = "APPLIES"
    POSSIBLY_APPLIES = "POSSIBLY_APPLIES"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
    DOES_NOT_APPLY = "DOES_NOT_APPLY"


class RuleStatus(str, enum.Enum):
    """Kun ``APPROVED`` regler indgår i en vurdering."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    DEPRECATED = "DEPRECATED"


class Operator(str, enum.Enum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    CONTAINS = "contains"


class FieldKind(str, enum.Enum):
    """Hvordan et profilfelt sammenlignes.

    Typen bestemmer, hvilke operatorer der giver mening. En regel, der
    spørger ``flag_state gt 'DK'``, er ikke en regel, men en fejl i
    modelleringen — den afvises ved udkast og giver ``NEEDS_MANUAL_REVIEW``,
    hvis den alligevel skulle nå frem til motoren.
    """

    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    TEXT_LIST = "text_list"


# ---------------------------------------------------------------------------
# Fartøjsprofil
# ---------------------------------------------------------------------------


class VesselProfile(BaseModel):
    """Skibet, som brugeren beskriver det.

    **Et felt, brugeren ikke har oplyst, er ukendt — også når skemaet giver
    det en standardværdi.** ``max_passengers = 0`` og ``sub_types = []`` er
    standarder for formularens skyld, ikke oplysninger om skibet. Motoren
    læser ``model_fields_set`` og behandler et ikke-oplyst felt som
    manglende. Ellers ville en udeladt passagerkapacitet stille og roligt
    blive til "ingen passagerer" og give et skråsikkert ``DOES_NOT_APPLY``
    på en passagerskibsbestemmelse.

    Undtagelsen er ``assessment_date``: skæringsdatoen er en del af
    spørgsmålet, ikke af skibet, og dagsdato er et meningsfuldt svar.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Identitet og registrering
    imo_number: str | None = Field(default=None, pattern=r"^\d{7}$")
    vessel_name: str = Field(min_length=1, max_length=200)
    flag_state: str = Field(pattern=r"^[A-Za-z]{2}$", description="ISO 3166-1 alpha-2, fx 'DK'.")
    ship_register: str | None = Field(default=None, max_length=16, description="Fx 'DIS', 'DAS'.")
    primary_type: str = Field(min_length=1, max_length=64)
    sub_types: list[str] = Field(default_factory=list, max_length=20)

    # Tekniske data
    gross_tonnage: float = Field(ge=0)
    length_overall_m: float | None = Field(default=None, ge=0)
    length_rule_m: float | None = Field(default=None, ge=0)
    deadweight_tonnage: float | None = Field(default=None, ge=0)

    # Bemanding og drift
    max_passengers: int = Field(default=0, ge=0)
    crew_capacity: int | None = Field(default=None, ge=0)
    operating_area: str = Field(min_length=1, max_length=32)
    voyage_type: str | None = Field(default=None, max_length=64)

    # Datoer — afgørende for overgangsregler og "eksisterende skibe"
    keel_laid_date: date
    delivery_date: date | None = None
    major_conversion_date: date | None = None
    assessment_date: date = Field(default_factory=date.today)

    # Driftsmæssige risici
    carries_dangerous_goods: bool = False
    special_operations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("flag_state")
    @classmethod
    def _upper_flag(cls, value: str) -> str:
        return value.upper()

    @field_validator("sub_types", "special_operations")
    @classmethod
    def _bounded_items(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item or len(item) > 64:
                raise ValueError("listeelementer skal være 1-64 tegn")
        return value


#: Hvert felt, en betingelse må pege på, og hvordan det sammenlignes.
#: En test sikrer, at registeret dækker præcis profilens felter, så et nyt
#: felt ikke kan tilføjes profilen uden at tage stilling til dets type.
PROFILE_FIELD_KINDS: dict[str, FieldKind] = {
    "imo_number": FieldKind.TEXT,
    "vessel_name": FieldKind.TEXT,
    "flag_state": FieldKind.TEXT,
    "ship_register": FieldKind.TEXT,
    "primary_type": FieldKind.TEXT,
    "sub_types": FieldKind.TEXT_LIST,
    "gross_tonnage": FieldKind.NUMBER,
    "length_overall_m": FieldKind.NUMBER,
    "length_rule_m": FieldKind.NUMBER,
    "deadweight_tonnage": FieldKind.NUMBER,
    "max_passengers": FieldKind.NUMBER,
    "crew_capacity": FieldKind.NUMBER,
    "operating_area": FieldKind.TEXT,
    "voyage_type": FieldKind.TEXT,
    "keel_laid_date": FieldKind.DATE,
    "delivery_date": FieldKind.DATE,
    "major_conversion_date": FieldKind.DATE,
    "assessment_date": FieldKind.DATE,
    "carries_dangerous_goods": FieldKind.BOOLEAN,
    "special_operations": FieldKind.TEXT_LIST,
}


# ---------------------------------------------------------------------------
# Regel
# ---------------------------------------------------------------------------


class EvaluationCondition(BaseModel):
    """Én sammenligning: ``<felt> <operator> <målværdi>``."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="Feltnavn på VesselProfile, fx 'gross_tonnage'.")
    operator: Operator
    target_value: Any = Field(description="Værdien feltet sammenlignes med.")


class ApplicabilityRule(BaseModel):
    """En bestemmelses anvendelsesområde, udtrykt som data.

    Datogrænserne følger den formulering, lovgivningen næsten altid bruger:

    * ``valid_from`` / ``valid_to`` er begge **inklusive** — reglen gælder
      på sin første og på sin sidste dag.
    * ``keel_laid_after`` er **inklusiv** ("kølstrakt den 1. januar 2009
      eller senere"), ``keel_laid_before`` er **eksklusiv** ("kølstrakt før
      den 1. januar 2009"). Dermed kan to regler for nye og eksisterende
      skibe dele samme skæringsdato uden overlap og uden hul.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    legal_source_id: str = Field(min_length=1, max_length=128)
    citation: str = Field(min_length=1, max_length=500)
    topic_category: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: RuleStatus = RuleStatus.DRAFT
    source_url: str | None = Field(default=None, max_length=2000)

    #: Mindst én. En regel uden inklusionsbetingelser ville gælde for ethvert
    #: skib i verden, og det er langt oftere en glemt betingelse end en lov.
    inclusion_conditions: list[EvaluationCondition] = Field(min_length=1, max_length=50)
    exclusion_conditions: list[EvaluationCondition] = Field(default_factory=list, max_length=50)

    valid_from: date | None = None
    valid_to: date | None = None
    keel_laid_after: date | None = None
    keel_laid_before: date | None = None

    @model_validator(mode="after")
    def _ordered_dates(self) -> ApplicabilityRule:
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from ligger efter valid_to")
        if (
            self.keel_laid_after
            and self.keel_laid_before
            and self.keel_laid_after >= self.keel_laid_before
        ):
            raise ValueError("keel_laid_after skal ligge før keel_laid_before")
        return self


# ---------------------------------------------------------------------------
# Resultat
# ---------------------------------------------------------------------------


class ConditionOutcome(str, enum.Enum):
    MET = "MET"
    NOT_MET = "NOT_MET"
    #: Feltet er ikke oplyst. Motoren gætter ikke.
    MISSING = "MISSING"
    #: Betingelsen kan ikke evalueres: ukendt felt, forkert operator eller
    #: en målværdi af forkert type. En fejl i reglen, ikke i skibet.
    INVALID = "INVALID"


class ConditionTrace(BaseModel):
    """Én betingelse, som den faktisk blev evalueret."""

    kind: Literal["inclusion", "exclusion"]
    field: str
    operator: str
    target_value: Any
    actual_value: Any = None
    field_supplied: bool
    outcome: ConditionOutcome
    detail: str


class TraceStep(BaseModel):
    """Ét trin i den faste beslutningsvej."""

    step: Literal["temporal_validity", "keel_laid", "exclusions", "missing_data", "inclusions"]
    outcome: Literal["passed", "decided", "skipped", "flagged"]
    detail: str


class RuleEvaluationResult(BaseModel):
    rule_id: str
    citation: str
    legal_source_id: str
    topic_category: str
    verdict: Verdict
    reasons: list[str]
    missing_fields: list[str]
    source_url: str | None = None
    #: Profilfelter, der opfyldte en inklusionsbetingelse — "hvorfor gælder den".
    matched_attributes: dict[str, Any] = Field(default_factory=dict)
    steps: list[TraceStep]
    conditions: list[ConditionTrace]


class TopicGroup(BaseModel):
    topic_category: str
    counts: dict[str, int]
    results: list[RuleEvaluationResult]


class EvaluationReport(BaseModel):
    """Svaret på ``POST /api/v1/applicability/evaluate``.

    ``ruleset_fingerprint`` og ``profile_hash`` er tilsammen en fuld
    beskrivelse af input: samme to værdier giver samme rapport.
    """

    vessel_name: str
    imo_number: str | None
    assessment_date: date
    engine_version: str
    deterministic: Literal[True] = True
    used_language_model: Literal[False] = False
    ruleset_fingerprint: str
    profile_hash: str
    #: Felter motoren regnede som oplyst. Alt andet er ukendt.
    supplied_fields: list[str]
    rules_evaluated: int
    counts: dict[str, int]
    topics: list[TopicGroup]
    coverage_notice: str
    cached: bool = False

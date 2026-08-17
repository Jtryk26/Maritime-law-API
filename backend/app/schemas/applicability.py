"""Pydantic-skemaer for anvendelighedsvurdering.

API'ets kontrakt. SQLAlchemy-modeller og domænegenstande returneres aldrig
direkte — brugerfladen skal kunne bygges uden at kende motorens indre typer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.applicability import (
    ApplicabilityResult,
    Dimensions,
    Jurisdiction,
    Lifecycle,
    Measured,
    OperationType,
    Persons,
    RankedResult,
    ValueSource,
    VesselProfile,
    VesselType,
    explain_applicability,
    get_field_spec,
)
from app.services.applicability.fields import FIELD_REGISTRY
from app.services.applicability.profile import Cargo

__all__ = [
    "LEGAL_SOURCE_NOTICE",
    "MeasuredIn",
    "VesselProfileIn",
    "EvaluationRequest",
    "FieldSpecOut",
    "CitationOut",
    "ConditionOut",
    "DecisionStepOut",
    "ReasonOut",
    "SupportingFragmentOut",
    "RuleCardOut",
    "ApplicabilityResponse",
    "DraftRunOut",
    "ReviewQueueItemOut",
    "ReviewDecisionIn",
    "RuleDetailOut",
    "to_rule_card",
]

LEGAL_SOURCE_NOTICE = (
    "Dokumentdata er hentet fra Retsinformation. Kontrollér altid den gældende "
    "officielle tekst på Retsinformation ved juridisk anvendelse."
)

UNAPPROVED_NOTICE = (
    "ADVARSEL: dette resultat bygger på regeludkast, der endnu ikke er gennemgået "
    "af et menneske. Udkast må ikke lægges til grund for en juridisk vurdering."
)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class MeasuredIn(BaseModel):
    """En talværdi med sin oprindelse.

    Oprindelsen er ikke pynt: ved en grænseværdi behandles ``estimated``
    anderledes end ``certificate``.
    """

    value: float
    source: ValueSource = ValueSource.DECLARED
    note: str | None = None

    def to_domain(self) -> Measured:
        return Measured(value=self.value, source=self.source, note=self.note)


class DimensionsIn(BaseModel):
    length_overall_m: MeasuredIn | None = None
    length_rule_m: MeasuredIn | None = None
    breadth_m: MeasuredIn | None = None
    depth_m: MeasuredIn | None = None
    dimensionstal: MeasuredIn | None = None
    gross_tonnage: MeasuredIn | None = None
    deadweight_tonnes: MeasuredIn | None = None


class PersonsIn(BaseModel):
    passenger_count: MeasuredIn | None = None
    industrial_personnel: MeasuredIn | None = None
    crew_count: MeasuredIn | None = None


class JurisdictionIn(BaseModel):
    flag_state: str | None = Field(default=None, max_length=8)
    operating_areas: list[str] = Field(default_factory=list, max_length=20)
    port_states: list[str] = Field(default_factory=list, max_length=20)


class LifecycleIn(BaseModel):
    keel_laid_date: date | None = None
    delivery_date: date | None = None
    major_conversion_date: date | None = None


class CargoIn(BaseModel):
    cargo_types: list[str] = Field(default_factory=list, max_length=20)
    carries_dangerous_goods: bool | None = None


class VesselProfileIn(BaseModel):
    """Brugerens beskrivelse af skibet.

    Felter må mangle. Motoren gætter ikke — den svarer
    ``NEEDS_MANUAL_REVIEW`` og oplyser hvilket felt der afgør sagen.
    """

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(default="profil", max_length=64)
    vessel_name: str | None = Field(default=None, max_length=200)
    vessel_type: VesselType
    additional_vessel_types: list[VesselType] = Field(default_factory=list, max_length=6)
    operation_types: list[OperationType] = Field(default_factory=list, max_length=13)
    dimensions: DimensionsIn = Field(default_factory=DimensionsIn)
    persons: PersonsIn = Field(default_factory=PersonsIn)
    jurisdiction: JurisdictionIn = Field(default_factory=JurisdictionIn)
    lifecycle: LifecycleIn = Field(default_factory=LifecycleIn)
    cargo: CargoIn = Field(default_factory=CargoIn)
    attributes: dict[str, str | float | bool] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def _limit_attributes(cls, value: dict) -> dict:
        if len(value) > 25:
            raise ValueError("Højst 25 attributter pr. profil.")
        return value

    def to_domain(self, assessment_date: date | None = None) -> VesselProfile:
        def measured(item: MeasuredIn | None) -> Measured | None:
            return item.to_domain() if item else None

        return VesselProfile(
            profile_id=self.profile_id,
            vessel_name=self.vessel_name,
            vessel_type=self.vessel_type,
            additional_vessel_types=list(self.additional_vessel_types),
            operation_types=list(self.operation_types),
            assessment_date=assessment_date,
            dimensions=Dimensions(
                length_overall_m=measured(self.dimensions.length_overall_m),
                length_rule_m=measured(self.dimensions.length_rule_m),
                breadth_m=measured(self.dimensions.breadth_m),
                depth_m=measured(self.dimensions.depth_m),
                dimensionstal=measured(self.dimensions.dimensionstal),
                gross_tonnage=measured(self.dimensions.gross_tonnage),
                deadweight_tonnes=measured(self.dimensions.deadweight_tonnes),
            ),
            persons=Persons(
                passenger_count=measured(self.persons.passenger_count),
                industrial_personnel=measured(self.persons.industrial_personnel),
                crew_count=measured(self.persons.crew_count),
            ),
            jurisdiction=Jurisdiction(
                flag_state=self.jurisdiction.flag_state,
                operating_areas=list(self.jurisdiction.operating_areas),
                port_states=list(self.jurisdiction.port_states),
            ),
            lifecycle=Lifecycle(
                keel_laid_date=self.lifecycle.keel_laid_date,
                delivery_date=self.lifecycle.delivery_date,
                major_conversion_date=self.lifecycle.major_conversion_date,
            ),
            cargo=Cargo(
                cargo_types=list(self.cargo.cargo_types),
                carries_dangerous_goods=self.cargo.carries_dangerous_goods,
            ),
            attributes=dict(self.attributes),
        )


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: VesselProfileIn
    assessment_date: date | None = Field(
        default=None, description="Skæringsdato for gyldighed. Standard: i dag."
    )
    status_mode: Literal["current", "historical", "all"] = "current"
    treat_estimated_as_unknown: bool = Field(
        default=False,
        description="Streng revisionstilstand: skønnede måleværdier regnes som ukendte.",
    )
    include_non_applicable: bool = Field(
        default=True, description="Tag regler med, der ikke gælder, nederst i svaret."
    )
    with_supporting_text: bool = True


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


class FieldSpecOut(BaseModel):
    """Ét felt i profilen — nok til at bygge inddateringsformularen."""

    name: str
    data_type: str
    gate: str
    label: str
    label_en: str
    unit: str | None = None
    input_hint: str | None = None


class CitationOut(BaseModel):
    """Ordret skoptekst. Skal gengives uændret og markeres som citat."""

    citation_key: str
    ref: str
    text: str
    kind: str
    char_start: int | None = None
    char_end: int | None = None
    document_version_id: int | None = None
    text_hash: str | None = None


class ConditionOut(BaseModel):
    field: str
    label: str
    op: str
    expected: object = None
    actual: object = None
    actual_source: str | None = None
    result: str
    gate: str
    near_threshold: bool = False
    margin_to_threshold: float | None = None
    unknown_reason: str | None = None
    citation_key: str | None = None


class DecisionStepOut(BaseModel):
    order: int
    gate: str
    gate_label: str
    outcome: str
    summary: str
    citation_keys: list[str] = Field(default_factory=list)


class ReasonOut(BaseModel):
    tone: str
    text: str
    ref: str | None = None
    quote: str | None = None
    citation_key: str | None = None


class MissingInputOut(BaseModel):
    field: str
    label: str
    hint: str | None = None
    unit: str | None = None
    data_type: str


class SupportingFragmentOut(BaseModel):
    """Fundet ved søgning. Har IKKE påvirket afgørelsen."""

    ref: str
    text: str
    score: float
    method: str
    chunk_id: int
    influenced_verdict: bool = False
    badge: str = "Fundet ved søgning — har ikke påvirket afgørelsen"


class RuleCardOut(BaseModel):
    rule_id: int | None
    document_id: int
    rule_ref: str
    title: str
    authority: str | None = None
    document_type: str | None = None
    source_url: str | None = None
    document_version_id: int | None = None

    verdict: str
    verdict_label: str
    style_key: str
    confidence: int
    rank: int
    rank_score: float

    headline: str
    summary: str
    reasons: list[ReasonOut] = Field(default_factory=list)
    conditions: list[ConditionOut] = Field(default_factory=list)
    citations: list[CitationOut] = Field(default_factory=list)
    missing_inputs: list[MissingInputOut] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    decision_path: list[DecisionStepOut] = Field(default_factory=list)
    supporting_fragments: list[SupportingFragmentOut] = Field(default_factory=list)

    coverage_level: str
    coverage_gaps: int
    review_status: str
    rule_state: str
    in_force_from: date | None = None
    warnings: list[str] = Field(default_factory=list)
    audit_text: str


class ApplicabilityResponse(BaseModel):
    profile_id: str
    assessment_date: date
    status_mode: str
    counts: dict[str, int]
    results: list[RuleCardOut]
    rules_evaluated: int
    legal_notice: str = LEGAL_SOURCE_NOTICE
    unapproved_notice: str | None = None
    engine: dict[str, object]


class DraftRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    scope: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    documents_scanned: int = 0
    rules_created: int = 0
    rules_unchanged: int = 0
    documents_without_scope: int = 0
    documents_failed: int = 0
    error_message: str | None = None
    trigger: str | None = None


class ReviewQueueItemOut(BaseModel):
    rule_id: int
    document_id: int
    document_title: str
    rule_ref: str
    review_status: str
    coverage_level: str
    coverage_gaps: int
    condition_count: int
    citation_count: int
    low_confidence_conditions: int
    created_at: datetime | None = None


class ReviewDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "rejected", "needs_changes", "draft"]
    actor: str = Field(max_length=128, description="Hvem traf afgørelsen. Gemmes i sporet.")
    note: str | None = Field(default=None, max_length=2000)
    coverage_level: Literal["complete", "partial", "unparsed"] | None = Field(
        default=None,
        description=(
            "Kan kun sættes her. 'complete' betyder, at et menneske har læst hele "
            "anvendelsesområdet igennem og står inde for modelleringen."
        ),
    )


class RuleConditionOut(BaseModel):
    id: int
    clause_kind: str
    clause_id: str | None = None
    parent_id: int | None = None
    node_type: str
    field: str | None = None
    op: str | None = None
    value: object = None
    citation_key: str | None = None
    unknown_policy: str
    tolerance: float | None = None
    draft_confidence: str | None = None
    note: str | None = None


class CoverageGapOut(BaseModel):
    citation_key: str | None = None
    reason: str
    resolved: bool = False


class RuleDetailOut(BaseModel):
    rule_id: int
    document_id: int
    document_title: str
    document_version_id: int | None = None
    rule_ref: str
    title: str
    authority: str | None = None
    document_type: str | None = None
    status_state: str
    in_force_from: date | None = None
    in_force_to: date | None = None
    coverage_level: str
    review_status: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    origin: str
    bindingness: int
    citations: list[CitationOut] = Field(default_factory=list)
    conditions: list[RuleConditionOut] = Field(default_factory=list)
    coverage_gaps: list[CoverageGapOut] = Field(default_factory=list)
    review_events: list[dict] = Field(default_factory=list)
    legal_notice: str = LEGAL_SOURCE_NOTICE


# ---------------------------------------------------------------------------
# Oversættelse fra domænet
# ---------------------------------------------------------------------------

_VERDICT_LABELS = {
    "APPLIES": ("Gælder", "applies"),
    "POSSIBLY_APPLIES": ("Gælder muligvis", "possibly"),
    "DOES_NOT_APPLY": ("Gælder ikke", "not-applicable"),
    "NEEDS_MANUAL_REVIEW": ("Kræver manuel vurdering", "review"),
}

_GATE_LABELS = {
    "temporal_status": "Gyldighed",
    "jurisdiction": "Jurisdiktion",
    "structured_metadata": "Struktureret metadata",
    "thresholds": "Tærskelværdier",
    "exclusions": "Undtagelser",
    "coverage": "Dækning",
}


def field_registry_out() -> list[FieldSpecOut]:
    return [
        FieldSpecOut(
            name=spec.name,
            data_type=spec.data_type.value,
            gate=spec.gate.value,
            label=spec.label_da,
            label_en=spec.label_en,
            unit=spec.unit,
            input_hint=spec.input_hint_da,
        )
        for spec in FIELD_REGISTRY.values()
    ]


def _condition_out(condition) -> ConditionOut:
    spec = get_field_spec(condition.field_name)
    return ConditionOut(
        field=condition.field_name,
        label=spec.label_da,
        op=condition.op.value,
        expected=condition.expected,
        actual=condition.actual,
        actual_source=condition.actual_source.value if condition.actual_source else None,
        result=condition.result.value,
        gate=condition.gate.value,
        near_threshold=condition.near_threshold,
        margin_to_threshold=condition.margin_to_threshold,
        unknown_reason=condition.unknown_reason.value if condition.unknown_reason else None,
        citation_key=condition.citation_key or None,
    )


def _warnings(result: ApplicabilityResult) -> list[str]:
    warnings: list[str] = []
    if result.is_synthetic:
        warnings.append(
            "Dette resultat bygger på systemets SYNTETISKE testdata og er ikke gældende ret."
        )
    if result.review_status != "approved":
        warnings.append(
            f"Reglen har status '{result.review_status}' og er ikke godkendt til brug."
        )
    if result.coverage_level != "complete":
        level = "kun delvist" if result.coverage_level == "partial" else "ikke"
        warnings.append(f"Anvendelsesområdet er {level} modelleret som data.")
    if result.rule_state == "repealed":
        warnings.append("Bestemmelsen er ophævet.")
    return warnings


def to_rule_card(entry: RankedResult) -> RuleCardOut:
    result = entry.result
    explanation = explain_applicability(result)
    label, style_key = _VERDICT_LABELS[result.verdict.value]

    return RuleCardOut(
        rule_id=result.rule_id,
        document_id=result.document_id,
        rule_ref=result.rule_ref,
        title=result.title,
        authority=result.authority,
        document_type=result.document_type,
        source_url=result.source_url,
        document_version_id=result.document_version_id,
        verdict=result.verdict.value,
        verdict_label=label,
        style_key=style_key,
        confidence=result.confidence,
        rank=entry.rank,
        rank_score=entry.rank_score,
        headline=explanation.headline,
        summary=explanation.summary,
        reasons=[
            ReasonOut(
                tone=item.tone,
                text=item.text,
                ref=item.ref,
                quote=item.quote,
                citation_key=item.citation_key,
            )
            for item in explanation.bullets
        ],
        conditions=[
            _condition_out(condition)
            for condition in (*result.matched, *result.failed, *result.unknown)
        ],
        citations=[
            CitationOut(
                citation_key=citation.key,
                ref=citation.ref,
                text=citation.text,
                kind=citation.kind.value,
                char_start=citation.char_start,
                char_end=citation.char_end,
                document_version_id=citation.document_version_id,
                text_hash=citation.text_hash,
            )
            for citation in result.citations
        ],
        missing_inputs=[
            MissingInputOut(
                field=name,
                label=get_field_spec(name).label_da,
                hint=get_field_spec(name).input_hint_da,
                unit=get_field_spec(name).unit,
                data_type=get_field_spec(name).data_type.value,
            )
            for name in result.missing_fields
        ],
        next_steps=[step.text for step in explanation.next_steps],
        decision_path=[
            DecisionStepOut(
                order=step.order,
                gate=step.gate.value,
                gate_label=_GATE_LABELS.get(step.gate.value, step.gate.value),
                outcome=step.outcome,
                summary=step.summary_da,
                citation_keys=step.citation_keys,
            )
            for step in result.decision_path
        ],
        supporting_fragments=[
            SupportingFragmentOut(
                ref=fragment.ref,
                text=fragment.text,
                score=fragment.score,
                method=fragment.method,
                chunk_id=fragment.chunk_id,
            )
            for fragment in result.supporting_fragments
        ],
        coverage_level=result.coverage_level.value,
        coverage_gaps=result.coverage_gap_count,
        review_status=result.review_status,
        rule_state=result.rule_state,
        in_force_from=result.in_force_from,
        warnings=_warnings(result),
        audit_text=explanation.plain_text,
    )

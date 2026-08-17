"""Beslutningsmotoren.

Rækkefølgen er fast og fremgår af ``decision_path`` i svaret::

    1. temporal_status     — gælder reglen på skæringsdatoen?
    2. jurisdiction        — flagstat og farvand
    3. structured_metadata — skibstype, operationstype, last
    4. thresholds          — længde, BT, dimensionstal, passagerantal
    5. exclusions          — undtagelsesbestemmelser
    6. coverage            — er hele anvendelsesområdet overhovedet modelleret?

Trin 4 køres kun, hvis trin 3 ikke allerede har udelukket reglen. Det er
meningen med "struktureret metadata først": en tærskelsammenligning kan hverken
redde eller vælte en regel, der ikke rammer skibstypen. Springes den over, står
det i beslutningsvejen som ``skipped``.

Der er ingen sprogmodel i denne fil og ingen andre steder i beslutningsvejen.
Ingen I/O, intet netværk, ingen tilfældighed: samme input giver samme afgørelse,
og ``audit.inputs_hash`` beviser det.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from .derive import DerivedFacts, derive_facts
from .fields import Gate
from .logic import (
    Atom,
    Comparator,
    ConditionNode,
    EvalContext,
    EvalOptions,
    EvaluatedCondition,
    Strength,
    UnknownReason,
    collect_atoms,
    evaluate_node,
)
from .profile import Tri, ValueSource, VesselProfile
from .rules import (
    ApplicabilityRuleSpec,
    CoverageLevel,
    DiscretionClause,
    DiscretionEffect,
    RuleState,
    ScopeCitation,
)

__all__ = [
    "ENGINE_VERSION",
    "Verdict",
    "ManualReviewCode",
    "DecisionStep",
    "ManualReviewReason",
    "SupportingFragment",
    "ApplicabilityResult",
    "evaluate_applicability",
    "evaluate_rules",
    "decide_verdict",
    "compute_confidence",
]

ENGINE_VERSION = "1.0.0"

#: Portene, hvis atomer hører til selve anvendelsesområdet. Et undtagelses-
#: eller skønsatom må ikke kunne blødgøre afgørelsen gennem grænsetolerancen.
INCLUSION_GATES = frozenset(
    {Gate.STRUCTURED_METADATA, Gate.THRESHOLDS, Gate.JURISDICTION}
)


class Verdict(str, enum.Enum):
    APPLIES = "APPLIES"
    POSSIBLY_APPLIES = "POSSIBLY_APPLIES"
    DOES_NOT_APPLY = "DOES_NOT_APPLY"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


class ManualReviewCode(str, enum.Enum):
    MISSING_PROFILE_DATA = "missing_profile_data"
    AMBIGUOUS_SCOPE_TEXT = "ambiguous_scope_text"
    UNPARSED_SCOPE = "unparsed_scope"
    UNKNOWN_RULE_STATUS = "unknown_rule_status"
    CONFLICTING_PROFILE_DATA = "conflicting_profile_data"
    UNKNOWN_EXCLUSION = "unknown_exclusion"
    HISTORICAL_RULE = "historical_rule"


@dataclass(slots=True)
class DecisionStep:
    order: int
    gate: Gate
    #: Tri-værdi, eller "skipped".
    outcome: str
    verdict_after: Verdict | None
    summary_da: str
    summary_en: str
    citation_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ManualReviewReason:
    code: ManualReviewCode
    detail_da: str
    detail_en: str
    fields: list[str] = field(default_factory=list)
    citation_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SupportingFragment:
    """Skopfragment fundet ved søgning. Uden for beslutningsvejen."""

    chunk_id: int
    document_id: int
    ref: str
    text: str
    score: float
    method: str
    document_version_id: int | None = None
    #: Altid falsk. Sættes af motoren, ikke af kalderen.
    influenced_verdict: bool = False


@dataclass(slots=True)
class ApplicabilityResult:
    rule_id: int | None
    document_id: int
    rule_ref: str
    title: str
    authority: str | None
    document_type: str | None
    profile_id: str

    verdict: Verdict
    confidence: int

    decision_path: list[DecisionStep] = field(default_factory=list)
    matched: list[EvaluatedCondition] = field(default_factory=list)
    failed: list[EvaluatedCondition] = field(default_factory=list)
    unknown: list[EvaluatedCondition] = field(default_factory=list)
    triggered_exclusions: list[tuple[str, str, Tri]] = field(default_factory=list)
    applicable_discretion: list[DiscretionClause] = field(default_factory=list)

    missing_fields: list[str] = field(default_factory=list)
    manual_review_reasons: list[ManualReviewReason] = field(default_factory=list)
    citations: list[ScopeCitation] = field(default_factory=list)

    coverage_level: CoverageLevel = CoverageLevel.UNPARSED
    coverage_gap_count: int = 0
    review_status: str = "draft"
    is_synthetic: bool = False
    source_url: str | None = None
    document_version_id: int | None = None
    historical: bool = False
    in_force_from: date | None = None
    rule_state: str = RuleState.UNKNOWN.value

    bindingness: int = 2
    specificity_score: int = 0

    #: Kun understøttende. Påvirker aldrig ``verdict``.
    supporting_fragments: list[SupportingFragment] = field(default_factory=list)

    engine_version: str = ENGINE_VERSION
    evaluated_at: datetime | None = None
    inputs_hash: str = ""
    assessment_date: date | None = None
    status_mode: str = "current"
    used_language_model: bool = False
    deterministic: bool = True


# ---------------------------------------------------------------------------
# Porte 1 og 2
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Temporal:
    outcome: Tri
    terminal: Verdict | None
    historical: bool
    summary_da: str
    summary_en: str


def _evaluate_temporal(rule: ApplicabilityRuleSpec, options: EvalOptions) -> _Temporal:
    day = options.assessment_date
    status = rule.status

    if status.state is RuleState.UNKNOWN:
        return _Temporal(
            Tri.UNKNOWN,
            Verdict.NEEDS_MANUAL_REVIEW,
            False,
            "Reglens gyldighedsstatus er ukendt og skal kontrolleres på Retsinformation.",
            "Rule status is unknown and must be checked against the official source.",
        )

    not_yet = status.in_force_from is not None and day < status.in_force_from
    expired = status.in_force_to is not None and day > status.in_force_to
    repealed = expired or status.state is RuleState.REPEALED

    if options.status_mode == "all":
        return _Temporal(
            Tri.TRUE,
            None,
            repealed,
            "Tidsfiltrering er slået fra (status_mode=all).",
            "Temporal filtering disabled (status_mode=all).",
        )

    if not_yet:
        return _Temporal(
            Tri.FALSE,
            Verdict.DOES_NOT_APPLY,
            False,
            f"Reglen træder først i kraft {status.in_force_from} og gælder ikke {day}.",
            f"Rule enters into force on {status.in_force_from}; not in force on {day}.",
        )

    if repealed and options.status_mode == "current":
        tail = f" med virkning fra {status.in_force_to}" if status.in_force_to else ""
        return _Temporal(
            Tri.FALSE,
            Verdict.DOES_NOT_APPLY,
            True,
            f"Reglen er ophævet{tail} og gælder ikke {day}.",
            "Rule was repealed and is not in force on the assessment date.",
        )

    if repealed and options.status_mode == "historical":
        return _Temporal(
            Tri.TRUE,
            None,
            True,
            f"Historisk regel: gjaldt indtil {status.in_force_to or 'ophævelsen'}.",
            "Historical rule: assessed as law as it stood on the assessment date.",
        )

    from_text = f" (i kraft fra {status.in_force_from})" if status.in_force_from else ""
    return _Temporal(
        Tri.TRUE,
        None,
        False,
        f"Reglen er gældende {day}{from_text}.",
        f"Rule is in force on {day}.",
    )


@dataclass(slots=True)
class _Jurisdiction:
    outcome: Tri
    summary_da: str
    summary_en: str


def _evaluate_jurisdiction(
    profile: VesselProfile, rule: ApplicabilityRuleSpec
) -> _Jurisdiction:
    rule_flags = rule.jurisdiction.flag_states
    rule_areas = rule.jurisdiction.operating_areas
    flag = profile.jurisdiction.flag_state
    areas = profile.jurisdiction.operating_areas
    ports = profile.jurisdiction.port_states

    if "*" in rule_flags:
        flag_match = Tri.TRUE
    elif not flag:
        flag_match = Tri.UNKNOWN
    else:
        flag_match = Tri.TRUE if flag in rule_flags else Tri.FALSE

    via_port_state = False
    if flag_match is Tri.FALSE and rule.jurisdiction.port_state_applies:
        if any(p in rule_flags for p in ports):
            flag_match = Tri.TRUE
            via_port_state = True

    if "*" in rule_areas:
        area_match = Tri.TRUE
    elif not areas:
        area_match = Tri.UNKNOWN
    else:
        area_match = Tri.TRUE if any(a in rule_areas for a in areas) else Tri.FALSE

    if flag_match is Tri.FALSE or area_match is Tri.FALSE:
        outcome = Tri.FALSE
    elif flag_match is Tri.UNKNOWN or area_match is Tri.UNKNOWN:
        outcome = Tri.UNKNOWN
    else:
        outcome = Tri.TRUE

    if outcome is Tri.TRUE and via_port_state:
        return _Jurisdiction(
            outcome,
            f"Omfattet gennem havnestatskontrol (anløb: {', '.join(ports)}).",
            "Covered via port state control.",
        )
    if outcome is Tri.TRUE:
        return _Jurisdiction(
            outcome,
            f"Flagstat og farvand er omfattet ({', '.join(rule_flags)} / {', '.join(rule_areas)}).",
            "Flag state and operating area are within scope.",
        )
    if outcome is Tri.FALSE:
        return _Jurisdiction(
            outcome,
            (
                "Uden for reglens geografiske anvendelsesområde (kræver flag "
                f"{', '.join(rule_flags)} og område {', '.join(rule_areas)})."
            ),
            "Outside the geographic scope of the rule.",
        )
    return _Jurisdiction(
        outcome,
        "Flagstat eller farvandsområde er ikke oplyst i profilen.",
        "Flag state or operating area missing from the profile.",
    )


# ---------------------------------------------------------------------------
# Hovedfunktionen
# ---------------------------------------------------------------------------


def evaluate_applicability(
    profile: VesselProfile,
    rule: ApplicabilityRuleSpec,
    options: EvalOptions | None = None,
) -> ApplicabilityResult:
    """Vurderer én regel mod én fartøjsprofil. Ren funktion."""
    options = options or EvalOptions(assessment_date=profile.assessment_date or date.today())
    derived = derive_facts(profile)
    trace: dict[str, EvaluatedCondition] = {}
    steps: list[DecisionStep] = []
    reasons: list[ManualReviewReason] = []
    used_keys: set[str] = set()

    def context(deferred: frozenset[Gate] = frozenset()) -> EvalContext:
        return EvalContext(
            profile=profile,
            derived=derived,
            options=options,
            deferred_gates=deferred,
            trace=trace,
        )

    # --- 1. Tid og status ------------------------------------------------
    temporal = _evaluate_temporal(rule, options)
    status_keys = [rule.status.citation_key] if rule.status.citation_key else []
    used_keys.update(status_keys)
    steps.append(
        DecisionStep(
            1,
            Gate.TEMPORAL_STATUS,
            temporal.outcome.value,
            temporal.terminal,
            temporal.summary_da,
            temporal.summary_en,
            status_keys,
        )
    )
    if temporal.terminal is not None:
        if temporal.terminal is Verdict.NEEDS_MANUAL_REVIEW:
            reasons.append(
                ManualReviewReason(
                    ManualReviewCode.UNKNOWN_RULE_STATUS,
                    temporal.summary_da,
                    temporal.summary_en,
                    [],
                    status_keys,
                )
            )
        return _finalize(
            temporal.terminal,
            profile,
            rule,
            derived,
            options,
            trace,
            steps,
            reasons,
            used_keys,
            [],
            [],
            temporal.historical,
        )

    # --- 2. Jurisdiktion --------------------------------------------------
    jurisdiction = _evaluate_jurisdiction(profile, rule)
    juris_keys = [rule.jurisdiction.citation_key] if rule.jurisdiction.citation_key else []
    used_keys.update(juris_keys)
    juris_terminal = (
        Verdict.DOES_NOT_APPLY
        if jurisdiction.outcome is Tri.FALSE
        else Verdict.NEEDS_MANUAL_REVIEW
        if jurisdiction.outcome is Tri.UNKNOWN
        else None
    )
    steps.append(
        DecisionStep(
            2,
            Gate.JURISDICTION,
            jurisdiction.outcome.value,
            juris_terminal,
            jurisdiction.summary_da,
            jurisdiction.summary_en,
            juris_keys,
        )
    )
    if juris_terminal is not None:
        if juris_terminal is Verdict.NEEDS_MANUAL_REVIEW:
            reasons.append(
                ManualReviewReason(
                    ManualReviewCode.MISSING_PROFILE_DATA,
                    jurisdiction.summary_da,
                    jurisdiction.summary_en,
                    ["jurisdiction.flag_state", "jurisdiction.operating_areas"],
                    juris_keys,
                )
            )
        return _finalize(
            juris_terminal,
            profile,
            rule,
            derived,
            options,
            trace,
            steps,
            reasons,
            used_keys,
            [],
            [],
            temporal.historical,
        )

    inclusion: ConditionNode | None = rule.inclusion

    # --- 3. Struktureret metadata (tærskler udskudt) ---------------------
    if inclusion is None:
        metadata_outcome = Tri.UNKNOWN
    else:
        metadata_outcome = evaluate_node(inclusion, context(frozenset({Gate.THRESHOLDS})))
    steps.append(
        DecisionStep(
            3,
            Gate.STRUCTURED_METADATA,
            metadata_outcome.value,
            Verdict.DOES_NOT_APPLY if metadata_outcome is Tri.FALSE else None,
            _gate_summary(trace, Gate.STRUCTURED_METADATA, metadata_outcome, "da"),
            _gate_summary(trace, Gate.STRUCTURED_METADATA, metadata_outcome, "en"),
            _gate_citation_keys(trace, Gate.STRUCTURED_METADATA),
        )
    )

    # --- 4. Tærskler ------------------------------------------------------
    if metadata_outcome is Tri.FALSE:
        inclusion_outcome = Tri.FALSE
        steps.append(
            DecisionStep(
                4,
                Gate.THRESHOLDS,
                "skipped",
                Verdict.DOES_NOT_APPLY,
                "Tærskelsammenligninger blev ikke udført: metadata udelukkede allerede reglen.",
                "Threshold comparisons skipped: metadata already excluded the rule.",
                [],
            )
        )
    else:
        inclusion_outcome = (
            Tri.UNKNOWN if inclusion is None else evaluate_node(inclusion, context())
        )
        steps.append(
            DecisionStep(
                4,
                Gate.THRESHOLDS,
                inclusion_outcome.value,
                Verdict.DOES_NOT_APPLY if inclusion_outcome is Tri.FALSE else None,
                _gate_summary(trace, Gate.THRESHOLDS, inclusion_outcome, "da"),
                _gate_summary(trace, Gate.THRESHOLDS, inclusion_outcome, "en"),
                _gate_citation_keys(trace, Gate.THRESHOLDS),
            )
        )

    used_keys.update(c.citation_key for c in trace.values() if c.citation_key)

    # --- 5. Undtagelser ---------------------------------------------------
    triggered: list[tuple[str, str, Tri]] = []
    if inclusion_outcome is not Tri.FALSE:
        for clause in rule.exclusions:
            outcome = evaluate_node(clause.condition, context())
            _retag(trace, clause.condition, Gate.EXCLUSIONS)
            used_keys.add(clause.citation_key)
            if outcome is not Tri.FALSE:
                triggered.append((clause.clause_id, clause.citation_key, outcome))

    excluded = any(o is Tri.TRUE for _, _, o in triggered)
    exclusion_unknown = any(o is Tri.UNKNOWN for _, _, o in triggered)
    steps.append(
        DecisionStep(
            5,
            Gate.EXCLUSIONS,
            "true" if excluded else "unknown" if exclusion_unknown else "false",
            Verdict.DOES_NOT_APPLY if excluded else None,
            (
                "En undtagelsesbestemmelse er opfyldt; reglen finder ikke anvendelse."
                if excluded
                else "En undtagelsesbestemmelse kunne ikke afgøres af de oplyste data."
                if exclusion_unknown
                else (
                    "Ingen undtagelsesbestemmelser er opfyldt."
                    if rule.exclusions
                    else "Bestemmelsen har ingen modellerede undtagelser."
                )
            ),
            (
                "An exclusion clause is satisfied; the rule does not apply."
                if excluded
                else "An exclusion clause could not be resolved."
                if exclusion_unknown
                else "No exclusion clauses satisfied."
            ),
            [key for _, key, _ in triggered],
        )
    )

    # --- 6. Skøn og dækning ----------------------------------------------
    applicable_discretion: list[DiscretionClause] = []
    for clause in rule.discretion:
        if clause.condition is None:
            in_play = True
        else:
            in_play = evaluate_node(clause.condition, context()) is not Tri.FALSE
            _retag(trace, clause.condition, Gate.COVERAGE)
        if in_play:
            applicable_discretion.append(clause)
            used_keys.add(clause.citation_key)

    verdict = decide_verdict(
        inclusion_outcome=inclusion_outcome,
        excluded=excluded,
        exclusion_unknown=exclusion_unknown,
        trace=trace,
        coverage_level=rule.coverage.level,
        applicable_discretion=applicable_discretion,
    )

    steps.append(
        DecisionStep(
            6,
            Gate.COVERAGE,
            "true" if rule.coverage.level is CoverageLevel.COMPLETE else "unknown",
            verdict,
            _coverage_summary(rule.coverage.level, len(applicable_discretion)),
            f"Coverage: {rule.coverage.level.value}.",
            [g.citation_key for g in rule.coverage.gaps if g.citation_key]
            + [c.citation_key for c in applicable_discretion],
        )
    )
    used_keys.update(g.citation_key for g in rule.coverage.gaps if g.citation_key)

    _collect_reasons(reasons, trace, rule, derived, exclusion_unknown, triggered)

    return _finalize(
        verdict,
        profile,
        rule,
        derived,
        options,
        trace,
        steps,
        reasons,
        used_keys,
        triggered,
        applicable_discretion,
        temporal.historical,
    )


def evaluate_rules(
    profile: VesselProfile,
    rules: list[ApplicabilityRuleSpec],
    options: EvalOptions | None = None,
) -> list[ApplicabilityResult]:
    return [evaluate_applicability(profile, rule, options) for rule in rules]


# ---------------------------------------------------------------------------
# Afgørelsestabellen
# ---------------------------------------------------------------------------


def decide_verdict(
    *,
    inclusion_outcome: Tri,
    excluded: bool,
    exclusion_unknown: bool,
    trace: dict[str, EvaluatedCondition],
    coverage_level: CoverageLevel,
    applicable_discretion: list[DiscretionClause],
) -> Verdict:
    """Hele tabellen i én læsbar funktion.

    Dette er den del, en jurist skal kunne efterprøve uden at læse resten af
    motoren. Den holdes derfor samlet, også hvor det koster lidt gentagelse.
    """
    atoms = [c for c in trace.values() if c.gate in INCLUSION_GATES]
    extend = [d for d in applicable_discretion if d.effect is DiscretionEffect.MAY_EXTEND]
    softening = [d for d in applicable_discretion if d.effect is not DiscretionEffect.MAY_EXTEND]

    if inclusion_outcome is Tri.FALSE:
        if extend:
            return Verdict.POSSIBLY_APPLIES
        # 499 BT mod grænsen "500 BT eller derover" er et nej, der hviler alene
        # på måleusikkerhed. Det afvises ikke lydløst.
        failed = [c for c in atoms if c.result is Tri.FALSE]
        if failed and all(c.near_threshold for c in failed):
            # ... men kun hvis grænsen ER det eneste, der står i vejen. Er noget
            # andet uafklaret, kan vi ikke engang vide, at det er et grænsenej.
            open_questions = [
                c
                for c in atoms
                if c.result is Tri.UNKNOWN
                and c.unknown_reason is not UnknownReason.DEFERRED_TO_LATER_GATE
            ]
            return (
                Verdict.NEEDS_MANUAL_REVIEW if open_questions else Verdict.POSSIBLY_APPLIES
            )
        return Verdict.DOES_NOT_APPLY

    if excluded:
        return Verdict.DOES_NOT_APPLY

    if coverage_level is CoverageLevel.UNPARSED:
        return Verdict.NEEDS_MANUAL_REVIEW

    if inclusion_outcome is Tri.UNKNOWN:
        return Verdict.NEEDS_MANUAL_REVIEW

    if (
        exclusion_unknown
        or coverage_level is CoverageLevel.PARTIAL
        or any(c.near_threshold for c in atoms)
        or softening
    ):
        return Verdict.POSSIBLY_APPLIES

    return Verdict.APPLIES


def compute_confidence(
    *,
    atoms: list[EvaluatedCondition],
    coverage_level: CoverageLevel,
    gap_count: int,
    discretion_count: int,
    conflict_count: int,
    historical: bool,
) -> int:
    """Konfidens er ikke sandsynlighed, men et fradragstal.

    Det viser, hvor meget usikkerhed der ligger bag afgørelsen.
    """
    score = 100
    if coverage_level is CoverageLevel.PARTIAL:
        score -= 25
    elif coverage_level is CoverageLevel.UNPARSED:
        score -= 50

    # Atomer, der aldrig blev vurderet, fordi en tidligere port afgjorde sagen,
    # er ikke usikkerhed — de er sparet arbejde.
    unknown = [
        c
        for c in atoms
        if c.result is Tri.UNKNOWN and c.unknown_reason is not UnknownReason.DEFERRED_TO_LATER_GATE
    ]
    score -= min(45, len(unknown) * 15)

    if any(c.near_threshold for c in atoms):
        score -= 10
    if any(c.actual_source is ValueSource.ESTIMATED for c in atoms):
        score -= 10
    score -= min(15, discretion_count * 5)
    score -= min(20, conflict_count * 20)
    if historical:
        score -= 10
    if gap_count:
        score -= min(10, gap_count * 2)

    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Hjælpere
# ---------------------------------------------------------------------------

_GATE_WEIGHT: dict[Gate, int] = {
    Gate.TEMPORAL_STATUS: 0,
    Gate.JURISDICTION: 1,
    Gate.STRUCTURED_METADATA: 3,
    Gate.THRESHOLDS: 2,
    Gate.EXCLUSIONS: 0,
    Gate.COVERAGE: 0,
}

_GATE_LABEL_DA: dict[Gate, str] = {
    Gate.TEMPORAL_STATUS: "Gyldighed",
    Gate.JURISDICTION: "Jurisdiktion",
    Gate.STRUCTURED_METADATA: "Struktureret metadata",
    Gate.THRESHOLDS: "Tærskelværdier",
    Gate.EXCLUSIONS: "Undtagelser",
    Gate.COVERAGE: "Dækning",
}


def _retag(
    trace: dict[str, EvaluatedCondition], node: ConditionNode | None, gate: Gate
) -> None:
    """Mærker undtagelses- og skønsatomer med deres egen port.

    "Skibet er ikke et fritidsfartøj" er ikke en fejlet betingelse i
    anvendelsesområdet — det er en undtagelse, der ikke slog til.
    """
    for atom in collect_atoms(node):
        existing = trace.get(atom.id)
        if existing is not None:
            existing.gate = gate


def _gate_summary(
    trace: dict[str, EvaluatedCondition], gate: Gate, outcome: Tri, lang: str
) -> str:
    atoms = [c for c in trace.values() if c.gate is gate]
    matched = sum(1 for c in atoms if c.result is Tri.TRUE)
    failed = sum(1 for c in atoms if c.result is Tri.FALSE)
    unknown = sum(1 for c in atoms if c.result is Tri.UNKNOWN)
    if lang == "da":
        word = {
            Tri.TRUE: "opfyldt",
            Tri.FALSE: "ikke opfyldt",
            Tri.UNKNOWN: "uafklaret",
        }[outcome]
        return (
            f"{_GATE_LABEL_DA[gate]}: {word} "
            f"({matched} opfyldt, {failed} ikke opfyldt, {unknown} uafklaret)."
        )
    word_en = {Tri.TRUE: "satisfied", Tri.FALSE: "not satisfied", Tri.UNKNOWN: "undetermined"}[
        outcome
    ]
    return f"{gate.value}: {word_en} ({matched} matched, {failed} failed, {unknown} unknown)."


def _gate_citation_keys(trace: dict[str, EvaluatedCondition], gate: Gate) -> list[str]:
    seen: list[str] = []
    for condition in trace.values():
        if condition.gate is gate and condition.citation_key and condition.citation_key not in seen:
            seen.append(condition.citation_key)
    return seen


def _coverage_summary(level: CoverageLevel, discretion: int) -> str:
    tail = f" {discretion} skønsbestemmelse(r) i spil." if discretion else ""
    if level is CoverageLevel.COMPLETE:
        return f"Hele anvendelsesområdet er modelleret og gennemgået.{tail}"
    if level is CoverageLevel.PARTIAL:
        return f"Anvendelsesområdet er kun delvist modelleret.{tail}"
    return f"Anvendelsesområdet er ikke modelleret som data.{tail}"


def _collect_reasons(
    reasons: list[ManualReviewReason],
    trace: dict[str, EvaluatedCondition],
    rule: ApplicabilityRuleSpec,
    derived: DerivedFacts,
    exclusion_unknown: bool,
    triggered: list[tuple[str, str, Tri]],
) -> None:
    atoms = list(trace.values())

    missing = [
        c
        for c in atoms
        if c.result is Tri.UNKNOWN and c.unknown_reason is UnknownReason.MISSING_PROFILE_FIELD
    ]
    if missing:
        names = ", ".join(sorted({c.field_name for c in missing}))
        reasons.append(
            ManualReviewReason(
                ManualReviewCode.MISSING_PROFILE_DATA,
                f"Profilen mangler oplysninger, som bestemmelsen bruger: {names}.",
                f"Profile is missing data used by the provision: {names}.",
                sorted({c.field_name for c in missing}),
                sorted({c.citation_key for c in missing if c.citation_key}),
            )
        )

    ambiguous = [
        c
        for c in atoms
        if c.result is Tri.UNKNOWN and c.unknown_reason is UnknownReason.UNSUPPORTED_COMPARISON
    ]
    if ambiguous:
        reasons.append(
            ManualReviewReason(
                ManualReviewCode.AMBIGUOUS_SCOPE_TEXT,
                "En betingelse kunne ikke sammenlignes maskinelt og skal læses manuelt.",
                "A condition could not be compared mechanically and must be read manually.",
                sorted({c.field_name for c in ambiguous}),
                sorted({c.citation_key for c in ambiguous if c.citation_key}),
            )
        )

    if rule.coverage.level is not CoverageLevel.COMPLETE:
        unparsed = rule.coverage.level is CoverageLevel.UNPARSED
        reasons.append(
            ManualReviewReason(
                ManualReviewCode.UNPARSED_SCOPE if unparsed else ManualReviewCode.AMBIGUOUS_SCOPE_TEXT,
                (
                    "Anvendelsesområdet er ikke modelleret som data. Læs § 1 i kilden."
                    if unparsed
                    else f"Dele af anvendelsesområdet er ikke modelleret ({len(rule.coverage.gaps)} led)."
                ),
                (
                    "Scope is not modelled as data."
                    if unparsed
                    else f"Parts of the scope are not modelled ({len(rule.coverage.gaps)} clauses)."
                ),
                [],
                [g.citation_key for g in rule.coverage.gaps if g.citation_key],
            )
        )

    if exclusion_unknown:
        reasons.append(
            ManualReviewReason(
                ManualReviewCode.UNKNOWN_EXCLUSION,
                "En undtagelsesbestemmelse kunne ikke afgøres. Kontrollér den mod profilen.",
                "An exclusion clause could not be resolved. Check it against the profile.",
                [],
                [key for _, key, outcome in triggered if outcome is Tri.UNKNOWN],
            )
        )

    used_fields = {c.field_name for c in atoms}
    for conflict in derived.conflicts:
        if any(f in used_fields for f in conflict.fields):
            reasons.append(
                ManualReviewReason(
                    ManualReviewCode.CONFLICTING_PROFILE_DATA,
                    conflict.detail_da,
                    conflict.detail_en,
                    list(conflict.fields),
                    [],
                )
            )


def _finalize(
    verdict: Verdict,
    profile: VesselProfile,
    rule: ApplicabilityRuleSpec,
    derived: DerivedFacts,
    options: EvalOptions,
    trace: dict[str, EvaluatedCondition],
    steps: list[DecisionStep],
    reasons: list[ManualReviewReason],
    used_keys: set[str],
    triggered: list[tuple[str, str, Tri]],
    discretion: list[DiscretionClause],
    historical: bool,
) -> ApplicabilityResult:
    atoms = list(trace.values())
    matched = [c for c in atoms if c.result is Tri.TRUE]
    failed = [c for c in atoms if c.result is Tri.FALSE]
    unknown = [c for c in atoms if c.result is Tri.UNKNOWN]

    final_verdict = verdict
    if historical and verdict in (Verdict.APPLIES, Verdict.POSSIBLY_APPLIES):
        final_verdict = Verdict.POSSIBLY_APPLIES
        reasons = [
            *reasons,
            ManualReviewReason(
                ManualReviewCode.HISTORICAL_RULE,
                "Vurderet som historisk ret. Bestemmelsen er ikke gældende i dag.",
                "Assessed as historical law. The provision is not currently in force.",
                [],
                [rule.status.citation_key] if rule.status.citation_key else [],
            ),
        ]

    missing_fields = sorted(
        {
            c.field_name
            for c in unknown
            if c.unknown_reason is UnknownReason.MISSING_PROFILE_FIELD
        }
    )
    conflicts = sum(
        1 for r in reasons if r.code is ManualReviewCode.CONFLICTING_PROFILE_DATA
    )
    specificity = sum(_GATE_WEIGHT.get(c.gate, 1) for c in matched) + rule.speciality_boost

    return ApplicabilityResult(
        rule_id=rule.rule_id,
        document_id=rule.document_id,
        rule_ref=rule.rule_ref,
        title=rule.title,
        authority=rule.authority,
        document_type=rule.document_type,
        profile_id=profile.profile_id,
        verdict=final_verdict,
        confidence=compute_confidence(
            atoms=atoms,
            coverage_level=rule.coverage.level,
            gap_count=len(rule.coverage.gaps),
            discretion_count=len(discretion),
            conflict_count=conflicts,
            historical=historical,
        ),
        decision_path=steps,
        matched=matched,
        failed=failed,
        unknown=unknown,
        triggered_exclusions=triggered,
        applicable_discretion=discretion,
        missing_fields=missing_fields,
        manual_review_reasons=reasons,
        citations=[c for key, c in rule.citations.items() if key in used_keys],
        coverage_level=rule.coverage.level,
        coverage_gap_count=len(rule.coverage.gaps),
        review_status=rule.review_status.value,
        is_synthetic=rule.is_synthetic,
        source_url=rule.source_url,
        document_version_id=rule.document_version_id,
        historical=historical,
        in_force_from=rule.status.in_force_from,
        rule_state=rule.status.state.value,
        bindingness=rule.bindingness,
        specificity_score=specificity,
        evaluated_at=options.now or datetime.now(timezone.utc),
        inputs_hash=_inputs_hash(profile, rule, options),
        assessment_date=options.assessment_date,
        status_mode=options.status_mode,
    )


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return str(value)


def _inputs_hash(
    profile: VesselProfile, rule: ApplicabilityRuleSpec, options: EvalOptions
) -> str:
    """SHA-256 over (profil, regel, indstillinger). Samme hash ⇒ samme afgørelse."""
    payload = {
        "profile": asdict(profile),
        "rule_id": rule.rule_id,
        "rule_ref": rule.rule_ref,
        "document_version_id": rule.document_version_id,
        "coverage": rule.coverage.level.value,
        "review_status": rule.review_status.value,
        "inclusion": _node_signature(rule.inclusion),
        "exclusions": [
            (c.clause_id, _node_signature(c.condition)) for c in rule.exclusions
        ],
        "discretion": [
            (c.clause_id, c.effect.value, _node_signature(c.condition)) for c in rule.discretion
        ],
        "options": {
            "assessment_date": options.assessment_date.isoformat(),
            "status_mode": options.status_mode,
            "default_tolerance_fraction": options.default_tolerance_fraction,
            "treat_estimated_as_unknown": options.treat_estimated_as_unknown,
        },
    }
    blob = json.dumps(payload, sort_keys=True, default=_json_default, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _node_signature(node: ConditionNode | None) -> object:
    if node is None:
        return None
    if isinstance(node, Atom):
        return [
            "atom",
            node.id,
            node.field_name,
            node.op.value,
            node.value,
            node.citation_key,
            node.strength.value,
            node.tolerance,
            node.unknown_policy.value,
        ]
    if isinstance(node, list):  # pragma: no cover - defensivt
        return [_node_signature(n) for n in node]
    kind = type(node).__name__
    children = getattr(node, "of", None)
    if isinstance(children, list):
        return [kind, [_node_signature(c) for c in children]]
    if children is not None:
        return [kind, _node_signature(children)]
    return [kind, getattr(node, "value", None)]


# Genudstilles for bekvemmelighed i tests og tilstødende moduler.
__all__ += ["Atom", "Comparator", "EvalOptions", "Strength", "Tri"]

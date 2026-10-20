"""Den deterministiske motor.

Beslutningsvejen er fast og står i hvert svars ``steps``::

    1. temporal_validity   Gælder reglen på skæringsdatoen?      nej → DOES_NOT_APPLY
    2. keel_laid           Ligger kølstrækningen i vinduet?       nej → DOES_NOT_APPLY
    3. exclusions          Er en undtagelse opfyldt?              ja  → DOES_NOT_APPLY
    4. missing_data        Mangler data til en nødvendig prøve?   ja  → NEEDS_MANUAL_REVIEW
    5. inclusions          Er alle inklusionsbetingelser opfyldt? nej → DOES_NOT_APPLY
                                                                  ja  → APPLIES

To præciseringer, som begge kun kan flytte svaret i den forsigtige retning:

**"Nødvendig prøve" i trin 4.** Inklusionsbetingelserne er forbundet med OG.
Er én af dem *beviseligt* ikke opfyldt — feltet er oplyst, og værdien falder
uden for — gælder reglen ikke, uanset hvad de manglende felter måtte vise. De
manglende felter er da ikke nødvendige for afgørelsen, og svaret er
``DOES_NOT_APPLY``. Alt andet manglende data giver ``NEEDS_MANUAL_REVIEW``,
også et manglende felt i en undtagelse: kan undtagelsen ikke afvises, kan
reglen ikke erklæres gældende.

**Væsentlig ombygning i trin 2.** Mange overgangsregler lader et skib, der
ombygges væsentligt, behandle som nyt. Ligger kølstrækningen uden for
vinduet, men ombygningsdatoen inden for, afvises reglen derfor ikke; den
prøves videre, og et ``APPLIES`` nedskrives til ``POSSIBLY_APPLIES``. Om en
ombygning er "væsentlig" i den konkrete forskrifts forstand, kan kun et
menneske afgøre.

En regel, hvis model ikke kan evalueres (ukendt felt, ugyldig operator, forkert
målværdi), giver altid ``NEEDS_MANUAL_REVIEW`` — aldrig ``DOES_NOT_APPLY``. En
defekt regel er fravær af viden, ikke viden om fravær.

Modulet har ingen I/O, intet ur og ingen tilfældighed. Skæringsdatoen kommer
fra profilen.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date
from typing import Any

from .models import (
    PROFILE_FIELD_ENUMS,
    PROFILE_FIELD_KINDS,
    ApplicabilityRule,
    ConditionOutcome,
    ConditionTrace,
    EvaluationCondition,
    FieldKind,
    Operator,
    RuleEvaluationResult,
    TraceStep,
    Verdict,
    VesselProfile,
)

__all__ = [
    "ENGINE_VERSION",
    "VALID_OPERATORS",
    "evaluate_condition",
    "evaluate_rule",
    "evaluate_rules",
    "ASSUMED_DEFAULTS",
    "assumed_default_fields",
    "supplied_fields",
    "validate_condition",
]

#: Indgår i regelsættets fingeraftryk. Ændres motorens semantik, skal tallet
#: op, så ingen gemt vurdering overlever en ændret fortolkning.
ENGINE_VERSION = "2.1.0"

_ORDERING = {Operator.GT, Operator.GTE, Operator.LT, Operator.LTE}
_EQUALITY = {Operator.EQ, Operator.NEQ}

#: Hvilke operatorer giver mening for hvilken felttype.
VALID_OPERATORS: dict[FieldKind, frozenset[Operator]] = {
    FieldKind.NUMBER: frozenset(_EQUALITY | _ORDERING | {Operator.IN}),
    FieldKind.DATE: frozenset(_EQUALITY | _ORDERING),
    FieldKind.TEXT: frozenset(_EQUALITY | {Operator.IN}),
    FieldKind.BOOLEAN: frozenset(_EQUALITY),
    FieldKind.TEXT_LIST: frozenset({Operator.CONTAINS, Operator.IN}),
    FieldKind.ENUM: frozenset(_EQUALITY | {Operator.IN}),
}

#: Felter med en standardværdi, der alligevel regnes som oplyst, når
#: brugeren ikke har sat dem. Se `VesselProfile`.
_DEFAULT_IS_KNOWN = frozenset({"assessment_date"})

#: Felter, hvis standardværdi motoren *antager*, når de ikke er oplyst — og
#: markerer som antaget i sporet. Listen er bevidst kort: hvert navn her er et
#: sted, hvor motoren lægger en antagelse til grund i stedet for en oplysning.
#: Brugt i en *inklusions*betingelse ville en antaget standard kunne give et
#: DOES_NOT_APPLY, der hviler på en antagelse; derfor afvises netop det ved
#: udkast (se `validate_condition`). Som undtagelse kan antagelsen højst give
#: et APPLIES for meget, og det er den forsigtige fejl.
ASSUMED_DEFAULTS = frozenset({"propulsion_type", "is_primitive_wooden_build"})


class _InvalidCondition(ValueError):
    """Betingelsen kan ikke evalueres. Altid en fejl i reglen."""


# ---------------------------------------------------------------------------
# Profilens kendte felter
# ---------------------------------------------------------------------------


def supplied_fields(profile: VesselProfile) -> frozenset[str]:
    """Felter, der faktisk bærer en oplysning om skibet.

    Et felt er oplyst, når det har en værdi (ikke ``None``) **og** enten blev
    sendt af brugeren eller er påkrævet i skemaet. En standardværdi, brugeren
    aldrig har set, er ikke en oplysning.
    """
    explicit = profile.model_fields_set
    known = set()
    for name, info in VesselProfile.model_fields.items():
        if getattr(profile, name) is None:
            continue
        if name in explicit or info.is_required() or name in _DEFAULT_IS_KNOWN:
            known.add(name)
    return frozenset(known)


def assumed_default_fields(profile: VesselProfile) -> frozenset[str]:
    """Felter i `ASSUMED_DEFAULTS`, brugeren ikke har oplyst."""
    return frozenset(ASSUMED_DEFAULTS - profile.model_fields_set)


# ---------------------------------------------------------------------------
# Typer og normalisering
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _norm_text(value: Any) -> str:
    if not isinstance(value, str):
        raise _InvalidCondition(f"forventede tekst, fik {type(value).__name__}")
    return value.strip().casefold()


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise _InvalidCondition(f"'{value}' er ikke en ISO-dato (ÅÅÅÅ-MM-DD)") from exc
    raise _InvalidCondition(f"forventede en dato, fik {type(value).__name__}")


def _coerce_target(kind: FieldKind, operator: Operator, target: Any) -> Any:
    """Målværdien i den form, sammenligningen kræver.

    Bruges både ved evaluering og ved validering af udkast, så en regel, der
    kan gemmes, også er en regel, der kan evalueres.
    """
    if kind is FieldKind.TEXT_LIST:
        if operator is Operator.CONTAINS:
            return _norm_text(target)
        return _target_list(target, _norm_text)

    if operator is Operator.IN:
        if kind is FieldKind.NUMBER:
            return _target_list(target, _number)
        return _target_list(target, _norm_text)

    if kind is FieldKind.NUMBER:
        return _number(target)
    if kind is FieldKind.DATE:
        return _as_date(target)
    if kind is FieldKind.BOOLEAN:
        if not isinstance(target, bool):
            raise _InvalidCondition(f"forventede true/false, fik {type(target).__name__}")
        return target
    return _norm_text(target)


def _number(value: Any) -> float:
    if not _is_number(value):
        raise _InvalidCondition(f"forventede et tal, fik {value!r}")
    return float(value)


def _target_list(target: Any, convert) -> list:
    if not isinstance(target, list) or not target:
        raise _InvalidCondition("operatoren 'in' kræver en ikke-tom liste som målværdi")
    return [convert(item) for item in target]


def _check_enum_target(field: str, operator: Operator, target: Any) -> None:
    allowed = PROFILE_FIELD_ENUMS.get(field)
    if allowed is None:
        return
    values = {member.value for member in allowed}
    for item in target if operator is Operator.IN else [target]:
        if item not in values:
            raise _InvalidCondition(
                f"'{item}' er ikke en gyldig værdi for '{field}'; "
                f"tilladt: {', '.join(sorted(values))}"
            )


def _check_operator(field: str, operator: Operator) -> FieldKind:
    kind = PROFILE_FIELD_KINDS.get(field)
    if kind is None:
        raise _InvalidCondition(f"'{field}' er ikke et felt på fartøjsprofilen")
    if operator not in VALID_OPERATORS[kind]:
        allowed = ", ".join(sorted(op.value for op in VALID_OPERATORS[kind]))
        raise _InvalidCondition(
            f"operatoren '{operator.value}' kan ikke bruges på '{field}' ({kind.value}); "
            f"tilladt: {allowed}"
        )
    return kind


def validate_condition(
    condition: EvaluationCondition, *, kind_label: str = "inclusion"
) -> str | None:
    """Fejlbeskrivelse, eller ``None`` hvis betingelsen kan evalueres."""
    try:
        _resolve(condition)
    except _InvalidCondition as exc:
        return str(exc)
    if kind_label == "inclusion" and condition.field in ASSUMED_DEFAULTS:
        return (
            f"'{condition.field}' har en antaget standardværdi og kan kun bruges i "
            "undtagelsesbetingelser; som inklusion kunne en antagelse give DOES_NOT_APPLY"
        )
    return None


def _resolve(condition: EvaluationCondition) -> tuple[FieldKind, Any]:
    kind = _check_operator(condition.field, condition.operator)
    target = _coerce_target(kind, condition.operator, condition.target_value)
    _check_enum_target(condition.field, condition.operator, target)
    return kind, target


# ---------------------------------------------------------------------------
# Én betingelse
# ---------------------------------------------------------------------------


def _compare(kind: FieldKind, operator: Operator, actual: Any, target: Any) -> bool:
    if kind is FieldKind.TEXT_LIST:
        values = {_norm_text(item) for item in actual}
        if operator is Operator.CONTAINS:
            return target in values
        return bool(values.intersection(target))  # IN: mindst ét fælles element

    if kind is FieldKind.NUMBER:
        actual = float(actual)
    elif kind in (FieldKind.TEXT, FieldKind.ENUM):
        actual = _norm_text(actual)

    if operator is Operator.IN:
        return actual in target
    if operator is Operator.EQ:
        return actual == target
    if operator is Operator.NEQ:
        return actual != target
    if operator is Operator.GT:
        return actual > target
    if operator is Operator.GTE:
        return actual >= target
    if operator is Operator.LT:
        return actual < target
    return actual <= target  # LTE


def evaluate_condition(
    condition: EvaluationCondition,
    profile: VesselProfile,
    known: frozenset[str],
    kind_label: str,
    assumed: frozenset[str] = frozenset(),
) -> ConditionTrace:
    is_assumed = condition.field in assumed
    supplied = condition.field in known
    usable = supplied or is_assumed
    actual = getattr(profile, condition.field, None) if usable else None

    def trace(outcome: ConditionOutcome, detail: str) -> ConditionTrace:
        return ConditionTrace(
            kind=kind_label,
            field=condition.field,
            operator=condition.operator.value,
            target_value=condition.target_value,
            actual_value=actual.isoformat() if isinstance(actual, date) else actual,
            field_supplied=supplied,
            assumed_default=is_assumed,
            outcome=outcome,
            detail=detail,
        )

    expression = f"{condition.field} {condition.operator.value} {condition.target_value!r}"
    problem = validate_condition(condition, kind_label=kind_label)
    if problem:
        return trace(
            ConditionOutcome.INVALID, f"{expression}: reglen er fejlmodelleret — {problem}"
        )
    kind, target = _resolve(condition)

    if not usable:
        return trace(ConditionOutcome.MISSING, f"{expression}: '{condition.field}' er ikke oplyst")

    met = _compare(kind, condition.operator, actual, target)
    shown = actual.isoformat() if isinstance(actual, date) else actual
    return trace(
        ConditionOutcome.MET if met else ConditionOutcome.NOT_MET,
        f"{expression}: {'antaget standardværdi' if is_assumed else 'faktisk værdi'} "
        f"{shown!r} → {'opfyldt' if met else 'ikke opfyldt'}",
    )


# ---------------------------------------------------------------------------
# Én regel
# ---------------------------------------------------------------------------


def _temporal_step(rule: ApplicabilityRule, on: date) -> TraceStep:
    if rule.valid_from and on < rule.valid_from:
        return TraceStep(
            step="temporal_validity",
            outcome="decided",
            detail=f"Reglen træder først i kraft {rule.valid_from.isoformat()}; "
            f"skæringsdatoen er {on.isoformat()}.",
        )
    if rule.valid_to and on > rule.valid_to:
        return TraceStep(
            step="temporal_validity",
            outcome="decided",
            detail=f"Reglen gjaldt til og med {rule.valid_to.isoformat()}; "
            f"skæringsdatoen er {on.isoformat()}.",
        )
    window = _window(rule.valid_from, rule.valid_to, inclusive_end=True)
    return TraceStep(
        step="temporal_validity",
        outcome="passed",
        detail=f"Skæringsdatoen {on.isoformat()} ligger i gyldighedsperioden {window}.",
    )


def _in_keel_window(rule: ApplicabilityRule, day: date) -> bool:
    if rule.keel_laid_after and day < rule.keel_laid_after:
        return False
    return not (rule.keel_laid_before and day >= rule.keel_laid_before)


def _keel_step(rule: ApplicabilityRule, profile: VesselProfile) -> tuple[TraceStep, bool]:
    """Kølstrækningsvinduet. Returnerer også, om reglen kun nås via ombygning."""
    if rule.keel_laid_after is None and rule.keel_laid_before is None:
        return TraceStep(step="keel_laid", outcome="skipped",
                         detail="Reglen har ingen grænse for kølstrækning."), False

    window = _window(rule.keel_laid_after, rule.keel_laid_before, inclusive_end=False)
    keel = profile.keel_laid_date
    if _in_keel_window(rule, keel):
        return TraceStep(
            step="keel_laid",
            outcome="passed",
            detail=f"Kølstrakt {keel.isoformat()} — inden for {window}.",
        ), False

    conversion = profile.major_conversion_date
    if (
        conversion is not None
        and conversion <= profile.assessment_date
        and _in_keel_window(rule, conversion)
    ):
        return TraceStep(
            step="keel_laid",
            outcome="flagged",
            detail=f"Kølstrakt {keel.isoformat()} — uden for {window}, men væsentlig "
            f"ombygning {conversion.isoformat()} ligger inden for. Om ombygningen bringer "
            "skibet ind under reglen, kræver en konkret vurdering.",
        ), True

    return TraceStep(
        step="keel_laid",
        outcome="decided",
        detail=f"Kølstrakt {keel.isoformat()} — uden for {window}.",
    ), False


def _window(start: date | None, end: date | None, *, inclusive_end: bool) -> str:
    left = f"fra og med {start.isoformat()}" if start else "uden startdato"
    if end is None:
        right = "uden slutdato"
    elif inclusive_end:
        right = f"til og med {end.isoformat()}"
    else:
        right = f"før {end.isoformat()}"
    return f"[{left}, {right}]"


def evaluate_rule(rule: ApplicabilityRule, profile: VesselProfile) -> RuleEvaluationResult:
    """Afgør én regel for ét skib. Ren funktion."""
    known = supplied_fields(profile)
    assumed = assumed_default_fields(profile)
    steps: list[TraceStep] = []
    reasons: list[str] = []

    def result(
        verdict: Verdict,
        conditions: list[ConditionTrace],
        missing: Iterable[str] = (),
    ) -> RuleEvaluationResult:
        matched = {
            c.field: c.actual_value
            for c in conditions
            if c.kind == "inclusion" and c.outcome is ConditionOutcome.MET
        }
        return RuleEvaluationResult(
            rule_id=rule.rule_id,
            citation=rule.citation,
            legal_source_id=rule.legal_source_id,
            topic_category=rule.topic_category,
            verdict=verdict,
            reasons=reasons,
            missing_fields=sorted(set(missing)),
            source_url=rule.source_url,
            matched_attributes=matched,
            steps=steps,
            conditions=conditions,
        )

    # 1. Gyldighed --------------------------------------------------------------
    temporal = _temporal_step(rule, profile.assessment_date)
    steps.append(temporal)
    if temporal.outcome == "decided":
        reasons.append(temporal.detail)
        return result(Verdict.DOES_NOT_APPLY, [])

    # 2. Kølstrækning -----------------------------------------------------------
    keel, via_conversion = _keel_step(rule, profile)
    steps.append(keel)
    if keel.outcome == "decided":
        reasons.append(keel.detail)
        return result(Verdict.DOES_NOT_APPLY, [])

    # Alle betingelser evalueres, også efter at svaret er givet. Sporet skal
    # vise hele billedet, og det koster intet: der er ingen sideeffekter.
    exclusions = [
        evaluate_condition(c, profile, known, "exclusion", assumed)
        for c in rule.exclusion_conditions
    ]
    inclusions = [
        evaluate_condition(c, profile, known, "inclusion", assumed)
        for c in rule.inclusion_conditions
    ]
    conditions = inclusions + exclusions

    invalid = [c for c in conditions if c.outcome is ConditionOutcome.INVALID]
    if invalid:
        # En defekt regel kan ikke sige "gælder ikke". Den kan intet sige.
        steps.append(TraceStep(step="exclusions", outcome="skipped",
                               detail="Reglen kan ikke evalueres; se betingelserne."))
        reasons.extend(c.detail for c in invalid)
        reasons.append("Reglens model er ugyldig og skal rettes, før den kan anvendes.")
        return result(Verdict.NEEDS_MANUAL_REVIEW, conditions)

    # 3. Undtagelser -------------------------------------------------------------
    met_exclusions = [c for c in exclusions if c.outcome is ConditionOutcome.MET]
    if met_exclusions:
        steps.append(TraceStep(
            step="exclusions",
            outcome="decided",
            detail=f"{len(met_exclusions)} undtagelse(r) opfyldt.",
        ))
        reasons.extend(f"Undtaget: {c.detail}" for c in met_exclusions)
        return result(Verdict.DOES_NOT_APPLY, conditions)
    steps.append(TraceStep(
        step="exclusions",
        outcome="passed" if exclusions else "skipped",
        detail="Ingen undtagelse er opfyldt." if exclusions else "Reglen har ingen undtagelser.",
    ))

    # 4. Manglende data på nødvendige prøver ------------------------------------
    failed = [c for c in inclusions if c.outcome is ConditionOutcome.NOT_MET]
    missing = [c for c in conditions if c.outcome is ConditionOutcome.MISSING]
    if missing and not failed:
        fields = sorted({c.field for c in missing})
        steps.append(TraceStep(
            step="missing_data",
            outcome="decided",
            detail="Afgørelsen afhænger af felter, der ikke er oplyst: " + ", ".join(fields) + ".",
        ))
        reasons.extend(c.detail for c in missing)
        reasons.append("Oplys de manglende felter, eller vurder bestemmelsen manuelt.")
        return result(Verdict.NEEDS_MANUAL_REVIEW, conditions, fields)
    steps.append(TraceStep(
        step="missing_data",
        outcome="passed",
        detail=(
            "Alle nødvendige felter er oplyst."
            if not missing
            else "Manglende felter er uden betydning: en inklusionsbetingelse er "
            "beviseligt ikke opfyldt."
        ),
    ))

    # 5. Inklusion ----------------------------------------------------------------
    if failed:
        steps.append(TraceStep(
            step="inclusions",
            outcome="decided",
            detail=f"{len(failed)} af {len(inclusions)} inklusionsbetingelser er ikke opfyldt.",
        ))
        reasons.extend(f"Ikke omfattet: {c.detail}" for c in failed)
        return result(Verdict.DOES_NOT_APPLY, conditions)

    steps.append(TraceStep(
        step="inclusions",
        outcome="passed",
        detail=f"Alle {len(inclusions)} inklusionsbetingelser er opfyldt.",
    ))
    reasons.extend(f"Omfattet: {c.detail}" for c in inclusions)

    if via_conversion:
        reasons.append(keel.detail)
        return result(Verdict.POSSIBLY_APPLIES, conditions)
    return result(Verdict.APPLIES, conditions)


def evaluate_rules(
    rules: Iterable[ApplicabilityRule], profile: VesselProfile
) -> list[RuleEvaluationResult]:
    """Afgør alle regler; rækkefølgen er stabil (emne, regel-id)."""
    ordered = sorted(rules, key=lambda r: (r.topic_category, r.rule_id))
    return [evaluate_rule(rule, profile) for rule in ordered]

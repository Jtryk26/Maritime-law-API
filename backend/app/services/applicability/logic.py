"""Betingelsestræ, tre-værdi-logik og sammenligninger.

Alt evalueres i Kleene-logik::

    AND   falsk hvis ét led er falsk — også når andre led er ukendte
    OR    sand hvis ét led er sandt
    NOT   bevarer ukendt

Motoren gætter aldrig på et manglende felt. En regel kan dog vælge
``unknown_policy = "treat_as_false"``, når et ukendt felt per definition ikke
kan være opfyldt — f.eks. ``attr.institution_type`` på et skib.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime

from .derive import DerivedFacts
from .fields import DataType, FieldValue, Gate, get_field_spec, resolve_field
from .profile import Tri, ValueSource, VesselProfile

__all__ = [
    "Comparator",
    "UnknownPolicy",
    "Strength",
    "ConditionNode",
    "Atom",
    "AllOf",
    "AnyOf",
    "NotNode",
    "Always",
    "EvaluatedCondition",
    "EvalContext",
    "EvalOptions",
    "kleene_and",
    "kleene_or",
    "kleene_not",
    "evaluate_node",
    "collect_atoms",
    "compare",
]


class Comparator(str, enum.Enum):
    EQ = "eq"
    NEQ = "neq"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    BETWEEN = "between"
    IN = "in"
    NOT_IN = "not_in"
    INTERSECTS = "intersects"
    CONTAINS_ALL = "contains_all"
    BEFORE = "before"
    ON_OR_AFTER = "on_or_after"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


class UnknownPolicy(str, enum.Enum):
    UNKNOWN = "unknown"
    TREAT_AS_FALSE = "treat_as_false"
    TREAT_AS_TRUE = "treat_as_true"


class Strength(str, enum.Enum):
    HARD = "hard"
    DISCRETIONARY = "discretionary"


class UnknownReason(str, enum.Enum):
    MISSING_PROFILE_FIELD = "missing_profile_field"
    AMBIGUOUS_SCOPE = "ambiguous_scope"
    UNSUPPORTED_COMPARISON = "unsupported_comparison"
    DEFERRED_TO_LATER_GATE = "deferred_to_later_gate"


# ---------------------------------------------------------------------------
# Betingelsestræ
# ---------------------------------------------------------------------------


class ConditionNode:
    """Fælles ophav for knuderne i et betingelsestræ."""


@dataclass(slots=True)
class Atom(ConditionNode):
    """Den mindste sammenligning. Skal altid pege på ordret skoptekst."""

    id: str
    field_name: str
    op: Comparator
    value: object = None
    #: Nøglen på den citation, betingelsen er læst ud af. Påkrævet.
    citation_key: str = ""
    strength: Strength = Strength.HARD
    #: Absolut måleusikkerhedsbånd omkring en talgrænse, i feltets enhed.
    tolerance: float | None = None
    unknown_policy: UnknownPolicy = UnknownPolicy.UNKNOWN
    note: str | None = None


@dataclass(slots=True)
class AllOf(ConditionNode):
    of: list[ConditionNode] = field(default_factory=list)


@dataclass(slots=True)
class AnyOf(ConditionNode):
    of: list[ConditionNode] = field(default_factory=list)


@dataclass(slots=True)
class NotNode(ConditionNode):
    of: ConditionNode | None = None


@dataclass(slots=True)
class Always(ConditionNode):
    value: bool = True
    citation_key: str | None = None


# ---------------------------------------------------------------------------
# Kleene
# ---------------------------------------------------------------------------


def kleene_and(values: list[Tri]) -> Tri:
    if any(v is Tri.FALSE for v in values):
        return Tri.FALSE
    if any(v is Tri.UNKNOWN for v in values):
        return Tri.UNKNOWN
    return Tri.TRUE


def kleene_or(values: list[Tri]) -> Tri:
    if any(v is Tri.TRUE for v in values):
        return Tri.TRUE
    if any(v is Tri.UNKNOWN for v in values):
        return Tri.UNKNOWN
    return Tri.FALSE


def kleene_not(value: Tri) -> Tri:
    if value is Tri.TRUE:
        return Tri.FALSE
    if value is Tri.FALSE:
        return Tri.TRUE
    return Tri.UNKNOWN


# ---------------------------------------------------------------------------
# Evaluering
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EvalOptions:
    assessment_date: date
    status_mode: str = "current"
    #: Standard måleusikkerhed som andel af grænsen, når `tolerance` mangler.
    default_tolerance_fraction: float = 0.02
    #: Streng revisionstilstand: skønnede værdier regnes som ukendte.
    treat_estimated_as_unknown: bool = False
    now: datetime | None = None


@dataclass(slots=True)
class EvaluatedCondition:
    condition_id: str
    field_name: str
    op: Comparator
    expected: object
    actual: object
    actual_source: ValueSource | None
    gate: Gate
    result: Tri
    strength: Strength
    citation_key: str
    near_threshold: bool = False
    margin_to_threshold: float | None = None
    unknown_reason: UnknownReason | None = None


@dataclass(slots=True)
class EvalContext:
    profile: VesselProfile
    derived: DerivedFacts
    options: EvalOptions
    #: Porte, der er udskudt til en senere fase (metadata før tærskler).
    deferred_gates: frozenset[Gate] = frozenset()
    #: Opsamler alle atomvurderinger, senest vindende pr. id.
    trace: dict[str, EvaluatedCondition] = field(default_factory=dict)


def evaluate_node(node: ConditionNode, ctx: EvalContext) -> Tri:
    """Evaluerer et betingelsestræ og skriver hvert atom i sporet."""
    if isinstance(node, Always):
        return Tri.TRUE if node.value else Tri.FALSE
    if isinstance(node, AllOf):
        # Alle grene evalueres — vi vil have det fulde spor, ikke kortslutning.
        return kleene_and([evaluate_node(child, ctx) for child in node.of])
    if isinstance(node, AnyOf):
        return kleene_or([evaluate_node(child, ctx) for child in node.of])
    if isinstance(node, NotNode):
        if node.of is None:
            return Tri.UNKNOWN
        return kleene_not(evaluate_node(node.of, ctx))
    if isinstance(node, Atom):
        evaluated = evaluate_atom(node, ctx)
        ctx.trace[evaluated.condition_id] = evaluated
        return evaluated.result
    raise TypeError(f"Ukendt knudetype: {type(node).__name__}")


def collect_atoms(node: ConditionNode | None, out: list[Atom] | None = None) -> list[Atom]:
    out = [] if out is None else out
    if node is None:
        return out
    if isinstance(node, Atom):
        out.append(node)
    elif isinstance(node, (AllOf, AnyOf)):
        for child in node.of:
            collect_atoms(child, out)
    elif isinstance(node, NotNode):
        collect_atoms(node.of, out)
    return out


def evaluate_atom(atom: Atom, ctx: EvalContext) -> EvaluatedCondition:
    spec = get_field_spec(atom.field_name)

    def build(
        result: Tri,
        value: FieldValue,
        *,
        reason: UnknownReason | None = None,
        near: bool = False,
        margin: float | None = None,
    ) -> EvaluatedCondition:
        return EvaluatedCondition(
            condition_id=atom.id,
            field_name=atom.field_name,
            op=atom.op,
            expected=atom.value,
            actual=value.value,
            actual_source=value.source,
            gate=spec.gate,
            result=result,
            strength=atom.strength,
            citation_key=atom.citation_key,
            near_threshold=near,
            margin_to_threshold=margin,
            unknown_reason=reason,
        )

    if spec.gate in ctx.deferred_gates:
        return build(
            Tri.UNKNOWN, FieldValue(present=False), reason=UnknownReason.DEFERRED_TO_LATER_GATE
        )

    resolved = resolve_field(ctx.profile, ctx.derived, atom.field_name)

    if atom.op in (Comparator.EXISTS, Comparator.NOT_EXISTS):
        wanted = atom.op is Comparator.EXISTS
        return build(Tri.TRUE if resolved.present == wanted else Tri.FALSE, resolved)

    treat_as_missing = not resolved.present or (
        ctx.options.treat_estimated_as_unknown and resolved.source is ValueSource.ESTIMATED
    )
    if treat_as_missing:
        if atom.unknown_policy is UnknownPolicy.TREAT_AS_FALSE:
            return build(Tri.FALSE, resolved)
        if atom.unknown_policy is UnknownPolicy.TREAT_AS_TRUE:
            return build(Tri.TRUE, resolved)
        return build(Tri.UNKNOWN, resolved, reason=UnknownReason.MISSING_PROFILE_FIELD)

    result, reason = compare(spec.data_type, atom.op, resolved.value, atom.value)
    near, margin = _threshold_info(atom, resolved.value, ctx.options)
    return build(result, resolved, reason=reason, near=near, margin=margin)


# ---------------------------------------------------------------------------
# Sammenligninger
# ---------------------------------------------------------------------------

_UNSUPPORTED = (Tri.UNKNOWN, UnknownReason.UNSUPPORTED_COMPARISON)


def _ok(value: bool) -> tuple[Tri, None]:
    return (Tri.TRUE if value else Tri.FALSE, None)


def compare(
    data_type: DataType, op: Comparator, actual: object, expected: object
) -> tuple[Tri, UnknownReason | None]:
    """Sammenligner én værdi. Kan den ikke, siger den ukendt frem for at gætte."""
    if data_type is DataType.NUMBER:
        return _compare_number(op, actual, expected)
    if data_type is DataType.BOOLEAN:
        if not isinstance(actual, bool):
            return _UNSUPPORTED
        if op is Comparator.EQ:
            return _ok(actual == expected)
        if op is Comparator.NEQ:
            return _ok(actual != expected)
        return _UNSUPPORTED
    if data_type in (DataType.ENUM, DataType.STRING):
        return _compare_scalar_string(op, actual, expected)
    if data_type in (DataType.ENUM_SET, DataType.STRING_SET):
        return _compare_set(op, actual, expected)
    if data_type is DataType.DATE:
        return _compare_date(op, actual, expected)
    return _UNSUPPORTED


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _compare_number(
    op: Comparator, actual: object, expected: object
) -> tuple[Tri, UnknownReason | None]:
    left = _as_number(actual)
    if left is None:
        return _UNSUPPORTED

    if op is Comparator.BETWEEN:
        if not isinstance(expected, (list, tuple)) or len(expected) != 2:
            return _UNSUPPORTED
        low, high = _as_number(expected[0]), _as_number(expected[1])
        if low is None or high is None:
            return _UNSUPPORTED
        return _ok(low <= left <= high)

    if op in (Comparator.IN, Comparator.NOT_IN):
        if not isinstance(expected, (list, tuple)):
            return _UNSUPPORTED
        hit = any(_as_number(item) == left for item in expected)
        return _ok(hit if op is Comparator.IN else not hit)

    right = _as_number(expected)
    if right is None:
        return _UNSUPPORTED
    match op:
        case Comparator.LT:
            return _ok(left < right)
        case Comparator.LTE:
            return _ok(left <= right)
        case Comparator.GT:
            return _ok(left > right)
        case Comparator.GTE:
            return _ok(left >= right)
        case Comparator.EQ:
            return _ok(left == right)
        case Comparator.NEQ:
            return _ok(left != right)
    return _UNSUPPORTED


def _compare_scalar_string(
    op: Comparator, actual: object, expected: object
) -> tuple[Tri, UnknownReason | None]:
    if not isinstance(actual, str):
        return _UNSUPPORTED
    match op:
        case Comparator.EQ:
            return _ok(actual == expected)
        case Comparator.NEQ:
            return _ok(actual != expected)
        case Comparator.IN:
            if not isinstance(expected, (list, tuple)):
                return _UNSUPPORTED
            return _ok(actual in expected)
        case Comparator.NOT_IN:
            if not isinstance(expected, (list, tuple)):
                return _UNSUPPORTED
            return _ok(actual not in expected)
    return _UNSUPPORTED


def _compare_set(
    op: Comparator, actual: object, expected: object
) -> tuple[Tri, UnknownReason | None]:
    if not isinstance(actual, (list, tuple, set)):
        return _UNSUPPORTED
    have = set(actual)
    wanted = list(expected) if isinstance(expected, (list, tuple, set)) else [expected]

    # "*" i regelværdien betyder "et hvilket som helst".
    if "*" in wanted:
        if op in (Comparator.INTERSECTS, Comparator.IN, Comparator.CONTAINS_ALL):
            return _ok(True)
        if op is Comparator.NOT_IN:
            return _ok(False)

    match op:
        case Comparator.INTERSECTS | Comparator.IN:
            return _ok(any(w in have for w in wanted))
        case Comparator.NOT_IN:
            return _ok(not any(w in have for w in wanted))
        case Comparator.CONTAINS_ALL:
            return _ok(all(w in have for w in wanted))
        case Comparator.EQ:
            return _ok(have == set(wanted))
    return _UNSUPPORTED


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _compare_date(
    op: Comparator, actual: object, expected: object
) -> tuple[Tri, UnknownReason | None]:
    left = _as_date(actual)
    if left is None:
        return _UNSUPPORTED

    if op is Comparator.BETWEEN:
        if not isinstance(expected, (list, tuple)) or len(expected) != 2:
            return _UNSUPPORTED
        low, high = _as_date(expected[0]), _as_date(expected[1])
        if low is None or high is None:
            return _UNSUPPORTED
        return _ok(low <= left <= high)

    right = _as_date(expected)
    if right is None:
        return _UNSUPPORTED
    match op:
        case Comparator.BEFORE | Comparator.LT:
            return _ok(left < right)
        case Comparator.ON_OR_AFTER | Comparator.GTE:
            return _ok(left >= right)
        case Comparator.EQ:
            return _ok(left == right)
        case Comparator.NEQ:
            return _ok(left != right)
    return _UNSUPPORTED


def _threshold_info(
    atom: Atom, actual: object, options: EvalOptions
) -> tuple[bool, float | None]:
    """Måleusikkerhed omkring en talgrænse.

    499 BT mod grænsen "500 BT eller derover" er formelt et nej, men et nej der
    hviler alene på måleusikkerhed. Det afvises ikke lydløst.
    """
    left = _as_number(actual)
    if left is None:
        return (False, None)
    if atom.op not in (
        Comparator.LT,
        Comparator.LTE,
        Comparator.GT,
        Comparator.GTE,
        Comparator.EQ,
        Comparator.BETWEEN,
    ):
        return (False, None)

    bound: float | None = _as_number(atom.value)
    if bound is None and isinstance(atom.value, (list, tuple)) and len(atom.value) == 2:
        low, high = _as_number(atom.value[0]), _as_number(atom.value[1])
        if low is not None and high is not None:
            bound = low if abs(left - low) <= abs(left - high) else high
    if bound is None:
        return (False, None)

    margin = round(abs(left - bound), 3)
    tolerance = (
        atom.tolerance
        if atom.tolerance is not None
        else abs(bound) * options.default_tolerance_fraction
    )
    return (0 < margin <= tolerance, margin)

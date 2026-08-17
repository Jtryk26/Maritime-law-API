"""Forklaring af en afgørelse.

Læser resultatet; omskriver det ikke. Teksterne er skabeloner, ikke genereret
sprog, og hvert punkt bærer den ordrette lovtekst, det hviler på.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import ApplicabilityResult, Verdict
from .fields import Gate, get_field_spec
from .logic import Comparator, EvaluatedCondition, UnknownReason
from .profile import Tri
from .rules import DiscretionEffect

__all__ = [
    "VERDICT_LABELS_DA",
    "ExplanationBullet",
    "NextStep",
    "Explanation",
    "explain_applicability",
]

VERDICT_LABELS_DA: dict[Verdict, str] = {
    Verdict.APPLIES: "Gælder",
    Verdict.POSSIBLY_APPLIES: "Gælder muligvis",
    Verdict.DOES_NOT_APPLY: "Gælder ikke",
    Verdict.NEEDS_MANUAL_REVIEW: "Kræver manuel vurdering",
}

_OP_DA: dict[Comparator, str] = {
    Comparator.EQ: "er",
    Comparator.NEQ: "er ikke",
    Comparator.LT: "er under",
    Comparator.LTE: "er højst",
    Comparator.GT: "er over",
    Comparator.GTE: "er mindst",
    Comparator.BETWEEN: "ligger mellem",
    Comparator.IN: "er blandt",
    Comparator.NOT_IN: "er ikke blandt",
    Comparator.INTERSECTS: "omfatter mindst én af",
    Comparator.CONTAINS_ALL: "omfatter alle af",
    Comparator.BEFORE: "er før",
    Comparator.ON_OR_AFTER: "er på eller efter",
    Comparator.EXISTS: "er oplyst",
    Comparator.NOT_EXISTS: "er ikke oplyst",
}

_TONE_MARK = {
    "match": "✓",
    "mismatch": "✗",
    "unknown": "?",
    "excluded": "⊘",
    "info": "·",
}

_LEGAL_NOTICE = (
    "Kontrollér altid den gældende officielle tekst på Retsinformation ved "
    "juridisk anvendelse."
)


@dataclass(slots=True)
class ExplanationBullet:
    tone: str
    text: str
    citation_key: str | None = None
    ref: str | None = None
    quote: str | None = None


@dataclass(slots=True)
class NextStep:
    action: str
    text: str
    field_name: str | None = None
    citation_key: str | None = None


@dataclass(slots=True)
class Explanation:
    verdict: Verdict
    verdict_label: str
    headline: str
    summary: str
    bullets: list[ExplanationBullet] = field(default_factory=list)
    next_steps: list[NextStep] = field(default_factory=list)
    plain_text: str = ""


def _format_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "ja" if value else "nej"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _negate_da(text: str) -> str:
    if text.startswith("er ikke"):
        return text.replace("er ikke", "er", 1)
    for prefix, replacement in (
        ("er ", "er ikke "),
        ("ligger ", "ligger ikke "),
        ("omfatter ", "omfatter ikke "),
    ):
        if text.startswith(prefix):
            return replacement + text[len(prefix) :]
    return f"ikke {text}"


def _describe(condition: EvaluatedCondition, matched: bool) -> str:
    spec = get_field_spec(condition.field_name)
    op = _OP_DA.get(condition.op, condition.op.value)
    unit = f" {spec.unit}" if spec.unit else ""
    near = ""
    if condition.near_threshold and condition.margin_to_threshold is not None:
        near = f" (kun {condition.margin_to_threshold:g}{unit} fra grænsen)"
    verb = op if matched else _negate_da(op)
    return (
        f"{spec.label_da}: {_format_value(condition.actual)}{unit} {verb} "
        f"{_format_value(condition.expected)}{unit}{near}."
    )


def _describe_unknown(condition: EvaluatedCondition) -> str:
    spec = get_field_spec(condition.field_name)
    if condition.unknown_reason is UnknownReason.MISSING_PROFILE_FIELD:
        hint = f" {spec.input_hint_da}" if spec.input_hint_da else ""
        return f"{spec.label_da} er ikke oplyst, og bestemmelsen bruger feltet.{hint}"
    if condition.unknown_reason is UnknownReason.DEFERRED_TO_LATER_GATE:
        return f"{spec.label_da} blev ikke vurderet: metadata afgjorde sagen først."
    return f"{spec.label_da} kunne ikke sammenlignes maskinelt og skal læses manuelt."


def explain_applicability(result: ApplicabilityResult) -> Explanation:
    citations = {c.key: c for c in result.citations}
    bullets: list[ExplanationBullet] = []

    def bullet(tone: str, text: str, key: str | None) -> None:
        citation = citations.get(key) if key else None
        bullets.append(
            ExplanationBullet(
                tone=tone,
                text=text,
                citation_key=key,
                ref=citation.ref if citation else None,
                quote=citation.text if citation else None,
            )
        )

    for step in result.decision_path:
        if step.gate not in (Gate.TEMPORAL_STATUS, Gate.JURISDICTION):
            continue
        key = step.citation_keys[0] if step.citation_keys else None
        if step.outcome == "true":
            if step.gate is Gate.JURISDICTION:
                bullet("info", step.summary_da, key)
        else:
            bullet("mismatch" if step.outcome == "false" else "unknown", step.summary_da, key)

    # Undtagelses- og skønsatomer får deres egne punkter længere nede.
    def is_scope(condition: EvaluatedCondition) -> bool:
        return condition.gate not in (Gate.EXCLUSIONS, Gate.COVERAGE)

    for condition in filter(is_scope, result.matched):
        bullet("match", _describe(condition, True), condition.citation_key)
    for condition in filter(is_scope, result.failed):
        bullet("mismatch", _describe(condition, False), condition.citation_key)
    for condition in filter(is_scope, result.unknown):
        bullet("unknown", _describe_unknown(condition), condition.citation_key)

    for _clause_id, key, outcome in result.triggered_exclusions:
        if outcome is Tri.TRUE:
            bullet("excluded", "Skibet er omfattet af en undtagelsesbestemmelse.", key)
        else:
            bullet(
                "unknown",
                "En undtagelsesbestemmelse kunne ikke afgøres af de oplyste data.",
                key,
            )

    for clause in result.applicable_discretion:
        effect = {
            DiscretionEffect.MAY_EXTEND: "udvide anvendelsesområdet til dette skib",
            DiscretionEffect.MAY_EXEMPT: "fritage skibet helt eller delvist",
            DiscretionEffect.MAY_MODIFY: "fastsætte afvigende krav",
        }[clause.effect]
        bullet("info", f"{clause.authority} kan efter bestemmelsen {effect}.", clause.citation_key)

    for reason in result.manual_review_reasons:
        key = reason.citation_keys[0] if reason.citation_keys else None
        bullet("unknown", reason.detail_da, key)

    explanation = Explanation(
        verdict=result.verdict,
        verdict_label=VERDICT_LABELS_DA[result.verdict],
        headline=f"{VERDICT_LABELS_DA[result.verdict]}: {result.title} ({result.rule_ref})",
        summary=_summary(result),
        bullets=bullets,
        next_steps=_next_steps(result),
    )
    explanation.plain_text = _render(explanation, result)
    return explanation


def _summary(result: ApplicabilityResult) -> str:
    scope_matched = sum(1 for c in result.matched if c.gate not in (Gate.EXCLUSIONS, Gate.COVERAGE))
    scope_failed = sum(1 for c in result.failed if c.gate not in (Gate.EXCLUSIONS, Gate.COVERAGE))
    scope_unknown = sum(
        1 for c in result.unknown if c.gate not in (Gate.EXCLUSIONS, Gate.COVERAGE)
    )
    if result.verdict is Verdict.APPLIES:
        return (
            f"Alle {scope_matched} betingelser i anvendelsesområdet er opfyldt, "
            "og ingen undtagelse er i spil."
        )
    if result.verdict is Verdict.DOES_NOT_APPLY:
        if scope_failed:
            return f"{scope_failed} betingelse(r) i anvendelsesområdet er ikke opfyldt."
        return "Bestemmelsen er udelukket, allerede før betingelserne blev vurderet."
    if result.verdict is Verdict.POSSIBLY_APPLIES:
        return (
            "Bestemmelsen kan finde anvendelse, men afgørelsen hviler på et forbehold, "
            "der skal efterprøves mod kildeteksten."
        )
    return f"Afgørelsen kan ikke træffes maskinelt: {scope_unknown} betingelse(r) er uafklarede."


def _next_steps(result: ApplicabilityResult) -> list[NextStep]:
    steps: list[NextStep] = []
    for name in result.missing_fields:
        spec = get_field_spec(name)
        steps.append(
            NextStep(
                "supply_field",
                f"Oplys {spec.label_da.lower()} for at afgøre spørgsmålet.",
                field_name=name,
            )
        )
    for clause in result.applicable_discretion:
        steps.append(
            NextStep(
                "contact_authority",
                (
                    f"Afklar med {clause.authority}, om skønsbeføjelsen er udnyttet "
                    "for dette skib."
                ),
                citation_key=clause.citation_key,
            )
        )
    if result.coverage_gap_count:
        count = result.coverage_gap_count
        led = "det ene led" if count == 1 else f"de {count} led"
        steps.append(
            NextStep("verify_clause", f"Læs {led} i anvendelsesområdet, der ikke er modelleret."),
        )
    if result.verdict is not Verdict.DOES_NOT_APPLY:
        steps.append(NextStep("check_source", _LEGAL_NOTICE))
    return steps


def _render(explanation: Explanation, result: ApplicabilityResult) -> str:
    lines = [explanation.headline, "=" * min(80, len(explanation.headline))]
    head = f"{result.authority or 'Ukendt myndighed'} · {result.document_type or '—'}"
    lines.append(f"{head} · konfidens {result.confidence}/100")
    if result.is_synthetic:
        lines.append("ADVARSEL: SYNTETISK TESTDATA — ikke gældende ret.")
    if result.review_status != "approved":
        lines.append(
            f"ADVARSEL: reglen har status '{result.review_status}' og er ikke godkendt."
        )
    lines.extend(["", explanation.summary, ""])
    for item in explanation.bullets:
        lines.append(f"{_TONE_MARK.get(item.tone, '·')} {item.text}")
        if item.quote:
            lines.append(f'    {item.ref}: "{item.quote}"')
    if explanation.next_steps:
        lines.extend(["", "Næste skridt:"])
        lines.extend(f"  → {step.text}" for step in explanation.next_steps)
    lines.extend(
        [
            "",
            (
                f"Motor {result.engine_version} · deterministisk · ingen sprogmodel · "
                f"inputhash {result.inputs_hash} · skæringsdato {result.assessment_date}"
            ),
        ]
    )
    return "\n".join(lines)

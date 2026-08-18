"""Diagnostik: hvorfor fandt vi ikke et anvendelsesområde?

Read-only. Kommandoen rører hverken dokumenter, regler, indeks eller vektorer,
og den er skrevet til at blive kørt på produktion, før nogen bygger om.

To spørgsmål besvares:

1. **Hvorfor har et dokument intet udkast?** Årsagerne er gensidigt
   udelukkende og ordnet efter, hvad man kan gøre ved dem. "Teksten har ingen
   paragraffer" er et andet problem end "vi kiggede ikke langt nok ned", og kun
   det sidste kan rettes med en parameter.

2. **Hvad står der i de udkast, vi har?** Antal betingelser, hvilke felter,
   hvilke mangler. Et udkast med nul betingelser koster en anmelder lige så
   meget tid at åbne som et godt — men er værdiløst, og de bør derfor kunne
   findes og lægges til side samlet.

Den vigtigste udgang er `missed_markers`: en frekvenstabel over de vendinger,
lovteksten faktisk bruger dér, hvor vi ikke fandt et skop. Den er grundlaget
for at udvide mønstrene med målte forbedringer frem for gæt.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.text import fold
from app.models import (
    ApplicabilityCondition,
    ApplicabilityCoverageGap,
    ApplicabilityRule,
    Document,
    DocumentVersion,
)
from app.services.legal.structure import parse_legal_structure

from .drafting import (
    _MAX_SCOPE_PARAGRAPH_INDEX,
    _SCOPE_HEADINGS,
    build_rule_drafts,
    classify_unit,
    extract_conditions,
)
from .rules import CitationKind

__all__ = [
    "REASON_LABELS",
    "DocumentDiagnosis",
    "CorpusReport",
    "DraftReport",
    "analyze_corpus",
    "analyze_drafts",
    "render_corpus_report",
    "render_draft_report",
]


#: Årsagerne, ordnet efter hvad man kan stille op med dem.
REASON_LABELS: dict[str, str] = {
    "har_udkast": "Har udkast",
    "tom_tekst": "Versionen har ingen tekst",
    "ingen_paragraffer": "Ingen paragraffer i teksten (vejledning, bilag, tabel)",
    "skop_uden_for_vindue": "Skopmarkør findes, men uden for søgevinduet",
    "kun_definitioner": "Kun definitions-/skønsbestemmelser, intet anvendelsesområde",
    "ingen_skopmarkoer": "Paragraffer findes, men ingen genkendt skopvending",
}

#: Ord, der peger på et anvendelsesområde, men som vi ikke nødvendigvis matcher.
#: Bruges KUN til at mine kandidatvendinger — aldrig til at afgøre noget.
_CUE_WORDS = (
    "anvendelse",
    "anvendes",
    "gælder",
    "omfatter",
    "omfattet",
    "vedrører",
    "angår",
    "finder",
    "fastsætter",
    "fastsættes",
    "regler for",
    "krav til",
    "undtaget",
)

_WORD = r"[a-zA-ZæøåÆØÅ0-9§.,-]+"


@dataclass(slots=True)
class DocumentDiagnosis:
    document_id: int
    title: str
    document_type: str | None
    reason: str
    paragraph_count: int = 0
    #: Hvilken paragraf bar skoppet, når det lå uden for vinduet.
    scope_paragraph: str | None = None
    scope_paragraph_index: int | None = None
    sample: str = ""
    #: Ville teksten give mindst én betingelse, hvis vendingen blev genkendt?
    would_yield_conditions: bool = False


@dataclass(slots=True)
class CorpusReport:
    documents_total: int = 0
    reasons: Counter = field(default_factory=Counter)
    #: Årsag → dokumenttype → antal. Viser om hullet er ét dokumentformat.
    reason_by_type: dict[str, Counter] = field(default_factory=dict)
    #: Kandidatvendinger fra dokumenter uden genkendt skop, hyppigste først.
    missed_markers: Counter = field(default_factory=Counter)
    samples: dict[str, list[DocumentDiagnosis]] = field(default_factory=dict)
    #: Hvor mange ville få et udkast, hvis søgevinduet blev fjernet.
    recoverable_by_window: int = 0
    #: Af dem uden genkendt vending: hvor mange ville give en regel MED
    #: betingelser, hvis vendingen blev tilføjet — og hvor mange ville blot
    #: blive endnu et tomt udkast i køen.
    marker_would_yield_conditions: int = 0
    marker_would_yield_empty: int = 0

    @property
    def with_drafts(self) -> int:
        return self.reasons.get("har_udkast", 0)

    @property
    def without_drafts(self) -> int:
        return self.documents_total - self.with_drafts


@dataclass(slots=True)
class DraftReport:
    rules_total: int = 0
    by_review_status: Counter = field(default_factory=Counter)
    by_coverage_level: Counter = field(default_factory=Counter)
    #: Antal betingelser pr. regel, samlet i spande.
    by_condition_count: Counter = field(default_factory=Counter)
    field_frequency: Counter = field(default_factory=Counter)
    gap_reasons: Counter = field(default_factory=Counter)
    low_confidence_rules: int = 0
    zero_condition_rules: int = 0
    by_rule_ref: Counter = field(default_factory=Counter)
    #: Regler med mindst én tærskel OG mindst én skibstype — de anvendelige.
    actionable_rules: int = 0


# ---------------------------------------------------------------------------
# Korpus
# ---------------------------------------------------------------------------


def _mine_markers(text: str, into: Counter) -> None:
    """Opsamler den vending, teksten bruger omkring et skop-ord.

    Fire ord omkring signalordet er nok til at se mønsteret ("finder tilsvarende
    anvendelse for", "gælder tillige for") og kort nok til at samme formulering
    lander i samme spand.
    """
    folded = fold(text)
    for cue in _CUE_WORDS:
        for match in re.finditer(rf"\b{re.escape(fold(cue))}\b(?:\s+{_WORD}){{0,3}}", folded):
            phrase = re.sub(r"\d+", "N", match.group(0)).strip(" .,;:")
            if len(phrase.split()) >= 2:
                into[phrase] += 1


def _diagnose(document: Document, content: str) -> DocumentDiagnosis:
    title = (document.display_title or document.title or "")[:120]
    base = dict(
        document_id=document.id,
        title=title,
        document_type=document.document_type,
    )

    if not (content or "").strip():
        return DocumentDiagnosis(**base, reason="tom_tekst")

    structure = parse_legal_structure(content, document_title=title)
    if not structure.has_paragraphs:
        return DocumentDiagnosis(
            **base,
            reason="ingen_paragraffer",
            sample=structure.text[:200].replace("\n", " "),
        )

    paragraph_count = len(structure.paragraphs)

    # Ligger der et skop længere nede, end vi kigger? Det er den eneste årsag,
    # der kan rettes med en parameter — derfor måles den for sig.
    # Kun inclusion og exclusion ER et anvendelsesområde. En skønsbestemmelse
    # ("Søfartsstyrelsen kan ...") og en definition står oftest langt nede i
    # teksten, og talte de med, ville rapporten anbefale et bredere søgevindue,
    # som kun ville trække tilsyns- og dispensationsbestemmelser ind.
    scope_kinds = (CitationKind.INCLUSION, CitationKind.EXCLUSION)
    first_kind: CitationKind | None = None
    first_index: int | None = None
    first_paragraph = None
    for index, paragraph in enumerate(structure.paragraphs):
        kind = classify_unit(paragraph.text)
        if kind is None:
            continue
        if kind in scope_kinds:
            first_kind, first_index, first_paragraph = kind, index, paragraph
            break
        if first_kind is None:
            first_kind, first_index, first_paragraph = kind, index, paragraph

    if first_kind is None:
        first = structure.paragraphs[0]
        atoms, _ = extract_conditions(first.text, "probe")
        return DocumentDiagnosis(
            **base,
            reason="ingen_skopmarkoer",
            paragraph_count=paragraph_count,
            sample=first.text[:220].replace("\n", " "),
            would_yield_conditions=bool(atoms),
        )

    if first_kind not in scope_kinds:
        return DocumentDiagnosis(
            **base,
            reason="kun_definitioner",
            paragraph_count=paragraph_count,
            scope_paragraph=first_paragraph.paragraph_id if first_paragraph else None,
            scope_paragraph_index=first_index,
            sample=(first_paragraph.text[:220].replace("\n", " ") if first_paragraph else ""),
        )

    heading = " ".join(
        part.lower()
        for part in (first_paragraph.chapter_title, first_paragraph.heading)
        if part
    ) if first_paragraph else ""
    in_heading = any(word in heading for word in _SCOPE_HEADINGS)
    if first_index is not None and first_index > _MAX_SCOPE_PARAGRAPH_INDEX and not in_heading:
        return DocumentDiagnosis(
            **base,
            reason="skop_uden_for_vindue",
            paragraph_count=paragraph_count,
            scope_paragraph=first_paragraph.paragraph_id if first_paragraph else None,
            scope_paragraph_index=first_index,
            sample=(first_paragraph.text[:220].replace("\n", " ") if first_paragraph else ""),
        )

    # Skoppet blev fundet og udvalgt, men gav intet udkast — behandles som
    # "ingen genkendt vending", da betingelsesudtrækket er det, der fejlede.
    probe_text = first_paragraph.text if first_paragraph else ""
    atoms, _ = extract_conditions(probe_text, "probe")
    return DocumentDiagnosis(
        **base,
        reason="ingen_skopmarkoer",
        paragraph_count=paragraph_count,
        scope_paragraph=first_paragraph.paragraph_id if first_paragraph else None,
        scope_paragraph_index=first_index,
        sample=probe_text[:220].replace("\n", " "),
        would_yield_conditions=bool(atoms),
    )


def analyze_corpus(
    session: Session,
    *,
    scope: str = "maritime",
    limit: int | None = None,
    samples_per_reason: int = 5,
) -> CorpusReport:
    """Gennemgår korpus og forklarer, hvorfor hvert dokument står, hvor det står."""
    report = CorpusReport()

    stmt = (
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .order_by(Document.id)
    )
    if scope == "maritime":
        stmt = stmt.where(Document.is_maritime.is_(True))
    if limit:
        stmt = stmt.limit(limit)

    for document, version in session.execute(stmt):
        report.documents_total += 1
        try:
            drafts = build_rule_drafts(
                document_id=document.id,
                document_version_id=version.id,
                content=version.content,
                title=document.display_title or document.title,
            )
        except Exception:  # noqa: BLE001 - diagnostik må aldrig vælte på skæv tekst
            drafts = []

        if drafts:
            report.reasons["har_udkast"] += 1
            report.reason_by_type.setdefault("har_udkast", Counter())[
                document.document_type or "ukendt"
            ] += 1
            continue

        diagnosis = _diagnose(document, version.content)
        report.reasons[diagnosis.reason] += 1
        report.reason_by_type.setdefault(diagnosis.reason, Counter())[
            document.document_type or "ukendt"
        ] += 1

        if diagnosis.reason == "skop_uden_for_vindue":
            report.recoverable_by_window += 1
        if diagnosis.reason in ("ingen_skopmarkoer", "kun_definitioner") and diagnosis.sample:
            _mine_markers(diagnosis.sample, report.missed_markers)
            if diagnosis.would_yield_conditions:
                report.marker_would_yield_conditions += 1
            else:
                report.marker_would_yield_empty += 1

        bucket = report.samples.setdefault(diagnosis.reason, [])
        if len(bucket) < samples_per_reason:
            bucket.append(diagnosis)

    return report


# ---------------------------------------------------------------------------
# Udkast
# ---------------------------------------------------------------------------


def _bucket(count: int) -> str:
    if count == 0:
        return "0 betingelser"
    if count == 1:
        return "1 betingelse"
    if count == 2:
        return "2 betingelser"
    return "3+ betingelser"


def analyze_drafts(session: Session, *, review_status: str | None = None) -> DraftReport:
    """Gør status over, hvad der faktisk står i reglerne."""
    report = DraftReport()

    stmt = select(ApplicabilityRule)
    if review_status:
        stmt = stmt.where(ApplicabilityRule.review_status == review_status)

    rules = list(session.scalars(stmt))
    rule_ids = [rule.id for rule in rules]
    report.rules_total = len(rules)
    if not rules:
        return report

    atoms_by_rule: dict[int, list[ApplicabilityCondition]] = {}
    for condition in session.scalars(
        select(ApplicabilityCondition).where(
            ApplicabilityCondition.rule_id.in_(rule_ids),
            ApplicabilityCondition.node_type == "atom",
        )
    ):
        atoms_by_rule.setdefault(condition.rule_id, []).append(condition)

    gaps_by_rule: dict[int, list[ApplicabilityCoverageGap]] = {}
    for gap in session.scalars(
        select(ApplicabilityCoverageGap).where(
            ApplicabilityCoverageGap.rule_id.in_(rule_ids),
            ApplicabilityCoverageGap.resolved.is_(False),
        )
    ):
        gaps_by_rule.setdefault(gap.rule_id, []).append(gap)

    for rule in rules:
        atoms = atoms_by_rule.get(rule.id, [])
        inclusion = [a for a in atoms if a.clause_kind == "inclusion"]

        report.by_review_status[rule.review_status] += 1
        report.by_coverage_level[rule.coverage_level] += 1
        report.by_condition_count[_bucket(len(inclusion))] += 1
        report.by_rule_ref[rule.rule_ref] += 1

        if not inclusion:
            report.zero_condition_rules += 1
        if any(a.draft_confidence == "low" for a in atoms):
            report.low_confidence_rules += 1

        fields = {a.field_name for a in inclusion if a.field_name}
        for name in fields:
            report.field_frequency[name] += 1
        has_threshold = any(name.startswith(("dim.", "persons.")) for name in fields)
        has_type = "vessel.all_types" in fields or "operation.types" in fields
        if has_threshold and has_type:
            report.actionable_rules += 1

        for gap in gaps_by_rule.get(rule.id, []):
            # Tal varierer mellem regler; mønsteret er det interessante.
            report.gap_reasons[re.sub(r"\d+", "N", gap.reason)[:90]] += 1

    return report


# ---------------------------------------------------------------------------
# Fremvisning
# ---------------------------------------------------------------------------


def _bar(count: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return ""
    filled = round(width * count / total)
    return "█" * filled + "·" * (width - filled)


def render_corpus_report(report: CorpusReport, *, samples: bool = True) -> str:
    total = report.documents_total
    lines = [
        "",
        "KORPUSDÆKNING",
        "=" * 72,
        f"Dokumenter gennemgået : {total}",
        f"Heraf med udkast      : {report.with_drafts}"
        + (f" ({report.with_drafts / total:.0%})" if total else ""),
        f"Uden udkast           : {report.without_drafts}"
        + (f" ({report.without_drafts / total:.0%})" if total else ""),
        "",
        "Årsag:",
    ]
    for reason, count in report.reasons.most_common():
        label = REASON_LABELS.get(reason, reason)
        lines.append(f"  {label:<52} {count:>6}  {_bar(count, total)}")

    if report.recoverable_by_window:
        lines += [
            "",
            f"→ {report.recoverable_by_window} dokumenter har en skopvending længere nede i teksten,",
            f"  end søgevinduet rækker (nu: de første {_MAX_SCOPE_PARAGRAPH_INDEX + 1} bestemmelser).",
            "  Det er den ene årsag, der kan rettes med en parameter frem for nye mønstre.",
        ]

    top_types = report.reason_by_type.get("ingen_paragraffer", Counter()).most_common(5)
    if top_types:
        lines += ["", "Dokumenttyper uden paragraffer (de mest almindelige):"]
        lines += [f"  {name or 'ukendt':<52} {count:>6}" for name, count in top_types]

    if report.marker_would_yield_conditions or report.marker_would_yield_empty:
        total_marker = report.marker_would_yield_conditions + report.marker_would_yield_empty
        lines += [
            "",
            "HVAD EN NY VENDING VILLE GIVE",
            "-" * 72,
            f"  Regel MED betingelser : {report.marker_would_yield_conditions:>6}"
            + (f" ({report.marker_would_yield_conditions / total_marker:.0%})" if total_marker else ""),
            f"  Tomt udkast i køen    : {report.marker_would_yield_empty:>6}"
            + (f" ({report.marker_would_yield_empty / total_marker:.0%})" if total_marker else ""),
            "",
            "Det andet tal er prisen: en ny vending, der ikke også giver en betingelse,",
            "flytter arbejdet fra 'ikke fundet' til 'endnu et tomt udkast at åbne'.",
        ]

    if report.missed_markers:
        lines += [
            "",
            "KANDIDATVENDINGER (fra dokumenter uden genkendt skop)",
            "-" * 72,
            "Sådan formulerer teksten sig dér, hvor vi ikke fandt noget.",
            "Hyppige vendinger her er de billigste mønstre at tilføje.",
            "",
        ]
        for phrase, count in report.missed_markers.most_common(25):
            lines.append(f"  {count:>5}  {phrase}")

    if samples and report.samples:
        lines += ["", "STIKPRØVER", "-" * 72]
        for reason, items in report.samples.items():
            if reason == "har_udkast":
                continue
            lines.append(f"\n{REASON_LABELS.get(reason, reason)}:")
            for item in items:
                where = f" [{item.scope_paragraph}, nr. {item.scope_paragraph_index}]" if item.scope_paragraph else ""
                lines.append(f"  #{item.document_id} {item.title[:66]}{where}")
                if item.sample:
                    lines.append(f"      «{item.sample[:150]}»")

    return "\n".join(lines)


def render_draft_report(report: DraftReport) -> str:
    total = report.rules_total
    lines = ["", "UDKAST", "=" * 72, f"Regler i alt          : {total}"]
    if not total:
        return "\n".join(lines + ["", "Ingen regler i basen endnu."])

    lines += [
        f"Med nul betingelser   : {report.zero_condition_rules} ({report.zero_condition_rules / total:.0%})",
        f"Med lav tillid        : {report.low_confidence_rules} ({report.low_confidence_rules / total:.0%})",
        f"Type + tærskel        : {report.actionable_rules} ({report.actionable_rules / total:.0%})"
        "   ← de umiddelbart anvendelige",
        "",
        "Betingelser pr. regel:",
    ]
    for bucket in ("0 betingelser", "1 betingelse", "2 betingelser", "3+ betingelser"):
        count = report.by_condition_count.get(bucket, 0)
        lines.append(f"  {bucket:<52} {count:>6}  {_bar(count, total)}")

    lines += ["", "Status:"]
    lines += [
        f"  {status:<52} {count:>6}" for status, count in report.by_review_status.most_common()
    ]
    lines += ["", "Dækningsgrad:"]
    lines += [
        f"  {level:<52} {count:>6}" for level, count in report.by_coverage_level.most_common()
    ]

    if report.field_frequency:
        lines += ["", "Felter, der blev udtrukket (antal regler):"]
        lines += [
            f"  {name:<52} {count:>6}  {_bar(count, total)}"
            for name, count in report.field_frequency.most_common()
        ]

    if report.by_rule_ref:
        lines += ["", "Bestemmelse udkastet hører til (top 8):"]
        lines += [
            f"  {ref:<52} {count:>6}" for ref, count in report.by_rule_ref.most_common(8)
        ]

    if report.gap_reasons:
        lines += ["", "Mangler (mønster, antal):"]
        lines += [
            f"  {count:>5}  {reason}" for reason, count in report.gap_reasons.most_common(10)
        ]

    return "\n".join(lines)

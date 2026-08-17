"""Udkastgenerator: fra rigtig lovtekst til forslag til regler.

Strategien
==========

1. **Struktur.** Teksten læses med :func:`app.services.legal.structure.parse_legal_structure`
   — samme parser, der bygger søgeindekset. Der er ingen grund til at have to
   forskellige opfattelser af, hvor § 12 begynder.

2. **Udvælgelse.** Skopbærende bestemmelser er § 1, bestemmelser i et kapitel
   med "Anvendelsesområde" i overskriften, og bestemmelser med et
   skopmarkør-udtryk. Kun blandt lovens første bestemmelser: "gælder for" dybt
   inde i en teknisk paragraf er en henvisning, ikke et anvendelsesområde.

3. **Klassifikation** pr. stykke: inclusion, exclusion, discretion, definition.
   Negationen tjekkes **før** det positive udtryk, fordi "finder ikke
   anvendelse" indeholder "finder anvendelse".

4. **Udtræk** af talgrænser og skibstyper til udkast til betingelser. Kun en
   eksplicit komparator ("eller derover", "under", "mere end") giver et udkast
   med høj tillid.

5. **Dækningsregnskab.** Alt i et skopstykke, der ikke blev omsat til en
   betingelse, registreres som en mangel. Derfor kan et maskinelt udtrukket
   udkast **aldrig** få dækningsgraden ``complete``; det kræver en menneskelig
   godkendelse. Det er den vigtigste egenskab ved denne fil: den kan ikke lyve
   motoren til et rent ``APPLIES``.

Om positioner
=============
``parse_legal_structure`` normaliserer teksten, før den læses. Citaternes
``char_start``/``char_end`` peger derfor ind i ``normalize_legal_text(content)``
— ikke i den rå kolonne. Normaliseringen er deterministisk, så et citat kan
stadig efterprøves; man skal blot køre teksten gennem samme funktion først.
``text_hash`` gør det muligt at opdage, hvis citatet er drevet fra kilden.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from app.services.legal.structure import (
    LegalDocumentStructure,
    LegalParagraph,
    parse_legal_structure,
)

from .logic import Comparator
from .rules import CitationKind, CoverageLevel, DiscretionEffect

__all__ = [
    "DraftCitation",
    "DraftAtom",
    "DraftExclusion",
    "DraftDiscretion",
    "RuleDraft",
    "build_rule_drafts",
    "classify_unit",
    "extract_conditions",
    "SCOPE_MARKERS",
]


# ---------------------------------------------------------------------------
# Markører
# ---------------------------------------------------------------------------

_EXCLUSION_MARKERS = (
    "finder ikke anvendelse",
    "finder dog ikke anvendelse",
    "gælder ikke",
    "omfatter ikke",
    "er undtaget",
    "undtaget fra",
    "ikke omfattet af",
)

_DISCRETION_MARKERS = (
    "kan søfartsstyrelsen",
    "søfartsstyrelsen kan",
    "kan tillade",
    "kan dispensere",
    "kan fravige",
    "kan bestemme",
    "kan fritage",
    "efter ansøgning",
)

_DEFINITION_MARKERS = (
    "forstås ved",
    "forstås i denne",
    "defineres som",
    "betyder i denne",
)

_INCLUSION_MARKERS = (
    "finder anvendelse på",
    "finder anvendelse for",
    "finder tilsvarende anvendelse",
    "gælder for",
    "anvendes på",
    "omfatter",
)

SCOPE_MARKERS = _EXCLUSION_MARKERS + _DISCRETION_MARKERS + _INCLUSION_MARKERS

_SCOPE_HEADINGS = ("anvendelsesområde", "område og definitioner", "anvendelse")

#: Hvor langt inde i loven vi leder efter anvendelsesområdet. § 1 og de næste
#: par bestemmelser; derefter er "gælder for" en henvisning, ikke et skop.
_MAX_SCOPE_PARAGRAPH_INDEX = 4


# ---------------------------------------------------------------------------
# Udtræk
# ---------------------------------------------------------------------------

_LEAD_OPS: dict[str, Comparator] = {
    "mindst": Comparator.GTE,
    "højst": Comparator.LTE,
    "mere end": Comparator.GT,
    "større end": Comparator.GT,
    "mindre end": Comparator.LT,
    "over": Comparator.GT,
    "under": Comparator.LT,
    "op til": Comparator.LTE,
    "fra og med": Comparator.GTE,
}

_TRAIL_OPS: dict[str, Comparator] = {
    "eller derover": Comparator.GTE,
    "og derover": Comparator.GTE,
    "eller derunder": Comparator.LTE,
    "og derunder": Comparator.LTE,
    "eller mere": Comparator.GTE,
    "eller mindre": Comparator.LTE,
}

_LEAD_ALT = "|".join(sorted(_LEAD_OPS, key=len, reverse=True))
_TRAIL_ALT = "|".join(sorted(_TRAIL_OPS, key=len, reverse=True))
_NUM = r"(\d+(?:[.,]\d+)?)"

#: Enheder, vi kan læse en grænse for. Rækkefølgen er betydende: den mest
#: specifikke enhed først, så "bruttotonnage" ikke fanges af "m".
_QUANTITY_UNITS: tuple[tuple[str, str], ...] = (
    ("dim.gross_tonnage", r"bruttotonnage|brutto-?tonnage|\bBT\b"),
    ("dim.dimensionstal", r"dimensionstal"),
    ("persons.passenger_count", r"passagerer|passagerantal"),
    ("persons.industrial_personnel", r"industripersonel|industripersoner"),
    ("dim.length_overall_m", r"længde overalt"),
    ("dim.length_rule_m", r"længde|meter\b"),
)

_VESSEL_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ro-?ro-?passagerskib", ("ro_ro_passenger_ship",)),
    (
        "passagerskib",
        ("passenger_ship", "ro_ro_passenger_ship", "high_speed_passenger_craft"),
    ),
    ("olietankskib", ("oil_tanker",)),
    ("kemikalietankskib", ("chemical_tanker",)),
    ("gastankskib|gasskib", ("gas_carrier",)),
    ("tankskib", ("oil_tanker", "chemical_tanker", "gas_carrier")),
    ("containerskib", ("container_ship",)),
    ("bulkskib|massegodsskib", ("bulk_carrier",)),
    (
        "lastskib|handelsskib",
        ("general_cargo_ship", "container_ship", "bulk_carrier", "ro_ro_cargo_ship"),
    ),
    ("fiskeskib|fiskefartøj", ("fishing_vessel",)),
    ("offshorefartøj|forsyningsskib", ("offshore_support_vessel", "rov_support_vessel")),
    ("dykkerskib", ("dive_support_vessel",)),
    ("fritidsfartøj|lystfartøj", ("pleasure_craft",)),
)

#: "national fart" står inde i "international fart". Uden det negative
#: lookbehind ville en bestemmelse om international fart også blive læst som en
#: bestemmelse om indenrigsfart — og reglen ville ramme dobbelt så mange skibe,
#: som den gør.
_OPERATION_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\b(?:international fart|udenrigsfart)\b", ("international_voyage",)),
    (r"\b(?:indenrigsfart|(?<!inter)national fart)\b", ("domestic_voyage",)),
    (r"\bkystnær fart\b", ("near_coastal",)),
    (r"\bhavnefart\b", ("harbour_service",)),
    (r"\bfiskeri\b", ("fishing_operation",)),
)


@dataclass(slots=True)
class DraftCitation:
    citation_key: str
    ref: str
    text: str
    kind: CitationKind
    char_start: int
    char_end: int
    text_hash: str


@dataclass(slots=True)
class DraftAtom:
    atom_id: str
    field_name: str
    op: Comparator
    value: object
    citation_key: str
    confidence: str = "high"
    note: str | None = None


@dataclass(slots=True)
class DraftExclusion:
    clause_id: str
    citation_key: str
    atoms: list[DraftAtom]
    label: str | None = None


@dataclass(slots=True)
class DraftDiscretion:
    clause_id: str
    citation_key: str
    authority: str = "Søfartsstyrelsen"
    effect: DiscretionEffect = DiscretionEffect.MAY_EXEMPT
    label: str | None = None


@dataclass(slots=True)
class RuleDraft:
    """Et forslag til en regel. Ikke gældende, før et menneske godkender det."""

    document_id: int
    document_version_id: int | None
    rule_ref: str
    title: str
    authority: str | None = None
    document_type: str | None = None
    in_force_from: date | None = None
    status_state: str = "unknown"
    bindingness: int = 2

    citations: list[DraftCitation] = field(default_factory=list)
    inclusion_atoms: list[DraftAtom] = field(default_factory=list)
    exclusions: list[DraftExclusion] = field(default_factory=list)
    discretion: list[DraftDiscretion] = field(default_factory=list)
    coverage_level: CoverageLevel = CoverageLevel.UNPARSED
    coverage_gaps: list[tuple[str | None, str]] = field(default_factory=list)

    @property
    def has_conditions(self) -> bool:
        return bool(self.inclusion_atoms)


# ---------------------------------------------------------------------------
# Klassifikation
# ---------------------------------------------------------------------------


def classify_unit(text: str) -> CitationKind | None:
    """Hvilken slags skopbestemmelse er dette stykke?"""
    folded = text.lower()
    # Negationen først: "finder ikke anvendelse" indeholder "finder anvendelse".
    if any(marker in folded for marker in _EXCLUSION_MARKERS):
        return CitationKind.EXCLUSION
    if any(marker in folded for marker in _DISCRETION_MARKERS):
        return CitationKind.DISCRETION
    if any(marker in folded for marker in _DEFINITION_MARKERS):
        return CitationKind.DEFINITION
    if any(marker in folded for marker in _INCLUSION_MARKERS):
        return CitationKind.INCLUSION
    return None


@dataclass(slots=True)
class _Unit:
    """Et stykke — den enhed, skopudtrækket arbejder på."""

    ref: str
    key: str
    text: str
    char_start: int
    char_end: int


def _units(paragraph: LegalParagraph, text: str) -> list[_Unit]:
    """Paragraffen delt i stykker. Stk. 1 er teksten før første "Stk. 2"."""
    base_key = f"p{paragraph.number}{paragraph.letter or ''}"
    first_sub = paragraph.subsections[0] if paragraph.subsections else None
    stk1_end = first_sub.char_start if first_sub else paragraph.char_end

    units: list[_Unit] = []
    stk1_text = text[paragraph.char_start : stk1_end].strip()
    if stk1_text:
        units.append(
            _Unit(
                ref=f"{paragraph.paragraph_id}, stk. 1",
                key=f"{base_key}s1",
                text=stk1_text,
                char_start=paragraph.char_start,
                char_end=stk1_end,
            )
        )
    for subsection in paragraph.subsections:
        body = text[subsection.char_start : subsection.char_end].strip()
        if not body:
            continue
        units.append(
            _Unit(
                ref=f"{paragraph.paragraph_id}, stk. {subsection.number}",
                key=f"{base_key}s{subsection.number}",
                text=body,
                char_start=subsection.char_start,
                char_end=subsection.char_end,
            )
        )
    return units


# ---------------------------------------------------------------------------
# Udtræk af betingelser
# ---------------------------------------------------------------------------


def extract_conditions(
    unit_text: str, citation_key: str
) -> tuple[list[DraftAtom], list[tuple[int, int]]]:
    """Læser skibstyper, fartsområder og talgrænser ud af ét stykke.

    Returnerer atomerne og de tekstspænd, de blev læst af, så dækningsgraden
    kan gøres op bagefter.
    """
    atoms: list[DraftAtom] = []
    spans: list[tuple[int, int]] = []

    # --- Skibstyper ------------------------------------------------------
    # ALLE nævnte skibstyper tages med, ikke kun den første. "passagerskibe og
    # lastskibe med en bruttotonnage på 500 og derover" nævner to typer, og et
    # udtræk, der kun så den første, ville lade et lastskib slippe ud af en
    # bestemmelse, det klart er omfattet af. Det er en farligere fejl end at
    # udtrække for lidt, fordi udkastet så ser rigtigt ud.
    #
    # Overlappende træffere ignoreres, så "ro-ro-passagerskib" ikke også tælles
    # som "passagerskib": mønstrene står i faldende specificitet.
    vessel_types: list[str] = []
    vessel_spans: list[tuple[int, int]] = []
    for pattern, types in _VESSEL_TERMS:
        for match in re.finditer(pattern, unit_text, re.IGNORECASE):
            if any(match.start() < end and start < match.end() for start, end in vessel_spans):
                continue
            vessel_spans.append(match.span())
            for value in types:
                if value not in vessel_types:
                    vessel_types.append(value)

    if vessel_types:
        spans.extend(vessel_spans)
        atoms.append(
            DraftAtom(
                atom_id=f"{citation_key}-type",
                field_name="vessel.all_types",
                op=Comparator.INTERSECTS,
                value=sorted(vessel_types),
                citation_key=citation_key,
                confidence="high" if len(vessel_spans) == 1 else "low",
                note=None
                if len(vessel_spans) == 1
                else (
                    "Flere skibstyper nævnt i samme bestemmelse. Udkastet omfatter dem "
                    "alle; kontrollér om tærskler i teksten kun gælder nogle af dem."
                ),
            )
        )

    # --- Fartsområde ------------------------------------------------------
    operations: list[str] = []
    for pattern, values in _OPERATION_TERMS:
        match = re.search(pattern, unit_text, re.IGNORECASE)
        if match:
            spans.append(match.span())
            operations.extend(values)
    if operations:
        atoms.append(
            DraftAtom(
                atom_id=f"{citation_key}-operation",
                field_name="operation.types",
                op=Comparator.INTERSECTS,
                value=sorted(set(operations)),
                citation_key=citation_key,
            )
        )

    # --- Talgrænser -------------------------------------------------------
    for field_name, unit_alt in _QUANTITY_UNITS:
        if any(atom.field_name == field_name for atom in atoms):
            continue
        found = _extract_quantity(unit_text, unit_alt)
        if found is None:
            continue
        value, op, span, explicit = found
        spans.append(span)
        atoms.append(
            DraftAtom(
                atom_id=f"{citation_key}-{field_name.replace('.', '_')}",
                field_name=field_name,
                op=op,
                value=value,
                citation_key=citation_key,
                confidence="high" if explicit else "low",
                note=None
                if explicit
                else "Komparator ikke fundet i teksten — gæt, skal gennemgås.",
            )
        )

    return atoms, spans


def _extract_quantity(
    text: str, unit_alt: str
) -> tuple[float, Comparator, tuple[int, int], bool] | None:
    """Finder én talgrænse for en enhed. Begge ordstillinger dækkes."""
    patterns = (
        # "en bruttotonnage på 500 eller derover", "dimensionstal under 100"
        rf"(?:{unit_alt})[^.;:]{{0,32}}?(?:på\s+)?(?:(?P<lead>{_LEAD_ALT})\s+)?{_NUM}"
        rf"(?:\s+(?P<trail>{_TRAIL_ALT}))?",
        # "under 15 meter", "mere end 12 passagerer"
        rf"(?:(?P<lead>{_LEAD_ALT})\s+)?{_NUM}\s*(?:{unit_alt})"
        rf"(?:\s+(?P<trail>{_TRAIL_ALT}))?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        number = _first_number(match)
        if number is None:
            continue
        lead = (match.groupdict().get("lead") or "").lower().strip()
        trail = (match.groupdict().get("trail") or "").lower().strip()
        op = _TRAIL_OPS.get(trail) or _LEAD_OPS.get(lead)
        return (number, op or Comparator.GTE, match.span(), op is not None)
    return None


def _first_number(match: re.Match[str]) -> float | None:
    """Første talgruppe i træfferen, læst med dansk talskrivning.

    Kommaet er decimaltegn ("15,5"), og et punktum efterfulgt af præcis tre
    cifre er en tusindadskiller ("1.500"), ikke en decimal.
    """
    for group in match.groups():
        if not group or not re.fullmatch(r"\d+(?:[.,]\d+)?", group):
            continue
        if "," in group:
            return float(group.replace(".", "").replace(",", "."))
        if re.fullmatch(r"\d+\.\d{3}", group):
            return float(group.replace(".", ""))
        return float(group)
    return None


# ---------------------------------------------------------------------------
# Samlet kørsel
# ---------------------------------------------------------------------------


def _is_scope_paragraph(paragraph: LegalParagraph, index: int) -> bool:
    heading = " ".join(
        part.lower() for part in (paragraph.chapter_title, paragraph.heading) if part
    )
    if any(word in heading for word in _SCOPE_HEADINGS):
        return True
    if index > _MAX_SCOPE_PARAGRAPH_INDEX:
        return False
    if paragraph.number == "1" and not paragraph.letter:
        return True
    return classify_unit(paragraph.text) is not None


def build_rule_drafts(
    *,
    document_id: int,
    document_version_id: int | None,
    content: str,
    title: str,
    authority: str | None = None,
    document_type: str | None = None,
    in_force_from: date | None = None,
    status_state: str = "unknown",
    bindingness: int = 2,
    structure: LegalDocumentStructure | None = None,
) -> list[RuleDraft]:
    """Bygger udkast til regler for ét dokument. Kaster aldrig på skæv tekst."""
    structure = structure or parse_legal_structure(content, document_title=title)
    if not structure.has_paragraphs:
        return []

    drafts: list[RuleDraft] = []
    for index, paragraph in enumerate(structure.paragraphs):
        if not _is_scope_paragraph(paragraph, index):
            continue
        draft = _draft_for_paragraph(
            paragraph=paragraph,
            text=structure.text,
            document_id=document_id,
            document_version_id=document_version_id,
            title=title,
            authority=authority,
            document_type=document_type,
            in_force_from=in_force_from,
            status_state=status_state,
            bindingness=bindingness,
        )
        if draft is not None:
            drafts.append(draft)
    return drafts


def _draft_for_paragraph(
    *,
    paragraph: LegalParagraph,
    text: str,
    document_id: int,
    document_version_id: int | None,
    title: str,
    authority: str | None,
    document_type: str | None,
    in_force_from: date | None,
    status_state: str,
    bindingness: int,
) -> RuleDraft | None:
    units = _units(paragraph, text)
    if not units:
        return None

    draft = RuleDraft(
        document_id=document_id,
        document_version_id=document_version_id,
        rule_ref=paragraph.paragraph_id,
        title=title,
        authority=authority,
        document_type=document_type,
        in_force_from=in_force_from,
        status_state=status_state,
        bindingness=bindingness,
    )

    any_scope_text = False
    for unit in units:
        kind = classify_unit(unit.text)
        if kind is None:
            continue
        # En definitionsbestemmelse er kontekst, ikke et anvendelsesområde.
        # Uden denne skelnen ville § 2 ("I denne bekendtgørelse forstås ved …")
        # blive til en tom regel, som ingen kan tage stilling til.
        if kind is not CitationKind.DEFINITION:
            any_scope_text = True
        citation_key = unit.key
        draft.citations.append(
            DraftCitation(
                citation_key=citation_key,
                ref=unit.ref,
                text=unit.text,
                kind=kind,
                char_start=unit.char_start,
                char_end=unit.char_end,
                text_hash=hashlib.sha256(unit.text.encode("utf-8")).hexdigest(),
            )
        )

        if kind is CitationKind.DEFINITION:
            continue

        if kind is CitationKind.DISCRETION:
            draft.discretion.append(
                DraftDiscretion(
                    clause_id=f"{citation_key}-disc",
                    citation_key=citation_key,
                    label=unit.ref,
                )
            )
            draft.coverage_gaps.append(
                (
                    citation_key,
                    "Skønsbestemmelse — betingelsen for skønnet skal modelleres i hånden.",
                )
            )
            continue

        atoms, spans = extract_conditions(unit.text, citation_key)

        if kind is CitationKind.EXCLUSION:
            if atoms:
                draft.exclusions.append(
                    DraftExclusion(
                        clause_id=f"{citation_key}-excl",
                        citation_key=citation_key,
                        atoms=atoms,
                        label=unit.ref,
                    )
                )
            else:
                draft.coverage_gaps.append(
                    (citation_key, "Undtagelsesbestemmelse, hvis betingelse ikke kunne udtrækkes.")
                )
            continue

        draft.inclusion_atoms.extend(atoms)
        if not atoms:
            draft.coverage_gaps.append(
                (citation_key, "Ingen betingelse kunne udtrækkes af bestemmelsen.")
            )
        else:
            covered = _covered_fraction(unit.text, spans)
            if covered < 0.25:
                draft.coverage_gaps.append(
                    (
                        citation_key,
                        f"Kun {round(covered * 100)} % af bestemmelsens tekst blev omsat "
                        "til betingelser.",
                    )
                )
            for atom in atoms:
                if atom.confidence != "low":
                    continue
                # Manglen bærer atomets egen begrundelse, så anmelderen ved
                # præcis hvad der skal efterprøves — ikke bare at noget er usikkert.
                draft.coverage_gaps.append(
                    (
                        citation_key,
                        atom.note
                        or "En betingelse blev udtrukket uden entydigt grundlag i teksten.",
                    )
                )

    if not any_scope_text:
        return None

    # Dækningsgraden sættes ALDRIG til complete her. Det kræver en menneskelig
    # godkendelse — ellers bliver et regex-match til en juridisk konklusion.
    draft.coverage_level = (
        CoverageLevel.PARTIAL if draft.inclusion_atoms else CoverageLevel.UNPARSED
    )
    return draft


def _covered_fraction(text: str, spans: list[tuple[int, int]]) -> float:
    if not text:
        return 0.0
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged) / len(text)

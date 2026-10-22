"""Udtræk af dokumentrelationer fra lovtekst og kildemetadata.

Hvad der udtrækkes
==================
``empowered_by`` (hjemmel i)
    Indledningen til en bekendtgørelse siger, hvilken lov og hvilke
    paragraffer den er udstedt i medfør af::

        I medfør af § 5, stk. 3, og § 12 i lov om lodsning, jf.
        lovbekendtgørelse nr. 1629 af 20. december 2016, fastsættes:

    Én relation pr. paragraf, så en lovs paragraf kan vise netop de
    bekendtgørelser, der udmønter den.

``replaces`` (afløser — historisk forgænger)
    * En lovbekendtgørelse samler loven på ny og nævner den forrige:
      "Hermed bekendtgøres lov om X, jf. lovbekendtgørelse nr. N af …".
    * En bekendtgørelse, der "samtidig ophæver" en ældre bekendtgørelse,
      afløser den.

``repeals`` (ophæver)
    En ophævelsesakt ("Bekendtgørelse om ophævelse af …") ophæver uden at
    afløse. Den er ikke forgænger for noget.

``replaced_by`` / ``empowers``
    Den modsatte retning. Udledes normalt ved opslag (B afløser A ⇒ A er
    afløst af B), men gemmes, hvis kildens metadata selv angiver den.

Kildens metadata
================
Retsinformations XML-skema er ikke verificeret (se ``xml_parser``). Felter
med navne som "erstatter", "afloeser", "historik" eller "replacedby" læses
defensivt for ELI-links og accessionsnumre. Teksten er det sikre signal;
metadata supplerer.

Alt her er rene funktioner uden databaseadgang.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import fold, normalize_whitespace

__all__ = [
    "RelationType",
    "ExtractedRelation",
    "extract_relations",
    "extract_legal_basis",
    "law_title_key",
    "number_key",
]


class RelationType:
    """Gyldige værdier. Strenge, som i resten af skemaet (se ``LawClass``)."""

    REPEALS = "repeals"
    REPLACES = "replaces"
    REPLACED_BY = "replaced_by"
    EMPOWERED_BY = "empowered_by"
    EMPOWERS = "empowers"
    #: Dansk bekendtgørelse -> den autentiske (engelske) traktattekst, gemt
    #: som eget dokument. Oprettes af importen, ikke af tekstudtrækket.
    TRANSLATION_OF = "translation_of"
    #: Forskrift -> den traktat, den gennemfører.
    TREATY_BASIS = "treaty_basis"

    #: De typer, `extract_relations` finder i teksten. Kun dem erstattes ved
    #: en genopbygning; resten vedligeholdes af importen.
    EXTRACTED = (REPEALS, REPLACES, REPLACED_BY, EMPOWERED_BY, EMPOWERS)
    ALL = (*EXTRACTED, TRANSLATION_OF, TREATY_BASIS)


@dataclass(frozen=True, slots=True)
class ExtractedRelation:
    relation_type: str
    #: Maskinnøgle til opslag: "LBK 1629/2016", "1629/2016", "accn:B2016…"
    #: eller en lovtitel ("lov om sikkerhed til søs").
    target_reference: str
    #: Visningstekst, som henvisningen står i kilden.
    target_label: str
    #: "§ 3, stk. 1" ved hjemmel. None ellers.
    specific_section: str | None = None


# -- Nøgler -----------------------------------------------------------------

_TYPE_WORDS = {
    "lovbekendtgoerelse": "LBK",
    "bekendtgoerelse": "BEK",
    "lov": "LOV",
    "cirkulaere": "CIR",
    "vejledning": "VEJ",
}


def number_key(type_word: str | None, number: str, year: str | int | None) -> str:
    """"LBK 1629/2016". Uden kendt type: "1629/2016"."""
    code = _TYPE_WORDS.get(fold(type_word or ""), "")
    core = f"{int(number)}/{year}" if year else str(int(number))
    return f"{code} {core}".strip()


_LAW_TITLE_RE = re.compile(r"^(?:bekendtgørelse af )?(lov om (?!ændring af).+)$", re.IGNORECASE)


def law_title_key(title: str | None) -> str | None:
    """"lov om sikkerhed til søs" for både loven og dens lovbekendtgørelser.

    En ændringslov ("Lov om ændring af …") er ikke selve loven og får ingen
    nøgle — ellers ville bekendtgørelser blive knyttet til ændringen.
    """
    text = normalize_whitespace(title or "").strip().rstrip(".")
    match = _LAW_TITLE_RE.match(text)
    return match.group(1).lower() if match else None


# -- Hjemmel ----------------------------------------------------------------

#: Indledningen: alt mellem "I medfør af" og "fastsættes"/"bestemmes".
_PREAMBLE_RE = re.compile(
    r"I medfør af\s+(?P<body>.+?)\s*(?:,\s*)?(?:fastsættes|bestemmes|bekendtgøres)\b",
    re.IGNORECASE | re.DOTALL,
)

#: Én lov i indledningen med sine paragraffer foran sig.
#:   "… i lov om sikkerhed til søs, jf. lovbekendtgørelse nr. 1629 af 20. december 2016"
#:   "… i lov nr. 456 af 10. juni 2020 om havne"
_LAW_IN_PREAMBLE_RE = re.compile(
    r"\s+i\s+(?P<title>lov(?:\s+nr\.\s*(?P<law_no>\d+)\s+af\s+(?P<law_date>[^,]*?(?P<law_year>\d{4})))?"
    r"\s+om\s+.+?)"
    r"(?:,\s*jf\.\s*(?P<ref_word>lovbekendtgørelse|lov)\s+nr\.\s*(?P<ref_no>\d+)"
    r"\s+af\s+(?P<ref_date>[^,]*?(?P<ref_year>\d{4})))?"
    r"(?=\s*,|\s+og\s+§|\s*$)",
    re.IGNORECASE,
)

#: "§ 5, stk. 3" / "§ 24 b" / "§ 3, nr. 2".
_SECTION_RE = re.compile(
    r"§\s*(?P<number>\d+\s*[a-zæøå]?)\b(?P<sub>(?:,\s*stk\.\s*\d+)?(?:,\s*nr\.\s*\d+)?)",
    re.IGNORECASE,
)
#: Et paragrafnummer med evt. bogstav: "24 b", "12a". Bogstavet må ikke
#: efterfølges af endnu et bogstav — ellers læses "3 og" som "§ 3 o".
_NUMBER = r"\d+(?:\s*[a-zæøå](?![a-zæøå]))?"
#: "§§ 3 og 4" / "§§ 3, 4 og 7" / "§§ 3-5".
_MULTI_SECTION_RE = re.compile(
    rf"§§\s*(?P<list>{_NUMBER}(?:\s*(?:,|og|-|–)\s*{_NUMBER})*)", re.IGNORECASE
)


def _sections(text: str) -> list[str]:
    """Paragrafhenvisningerne i et stykke af indledningen, i rækkefølge."""
    found: list[tuple[int, str]] = []
    for match in _MULTI_SECTION_RE.finditer(text):
        for number in re.findall(_NUMBER, match.group("list"), re.IGNORECASE):
            found.append((match.start(), f"§ {normalize_whitespace(number)}"))
    multi_spans = [m.span() for m in _MULTI_SECTION_RE.finditer(text)]
    for match in _SECTION_RE.finditer(text):
        if any(start <= match.start() < end for start, end in multi_spans):
            continue
        sub = normalize_whitespace(match.group("sub") or "")
        found.append((match.start(), f"§ {normalize_whitespace(match.group('number'))}{sub}"))
    found.sort(key=lambda item: item[0])
    return [section for _, section in found]


def extract_legal_basis(content: str) -> list[ExtractedRelation]:
    """Hjemmelshenvisningerne i en bekendtgørelses indledning."""
    head = (content or "")[:4000]
    match = _PREAMBLE_RE.search(head)
    if not match:
        return []
    body = normalize_whitespace(match.group("body"))

    relations: list[ExtractedRelation] = []
    previous_end = 0
    for law in _LAW_IN_PREAMBLE_RE.finditer(body):
        sections = _sections(body[previous_end : law.start()])
        previous_end = law.end()
        title = normalize_whitespace(law.group("title"))
        # "lov nr. 456 af … om havne" -> titelnøglen er "lov om havne".
        title_key = re.sub(r"^lov\s+nr\.\s*\d+\s+af\s+.*?\d{4}\s+", "lov ", title,
                           flags=re.IGNORECASE).lower()

        if law.group("ref_no"):
            reference = number_key(law.group("ref_word"), law.group("ref_no"), law.group("ref_year"))
            label = (
                f"{title}, jf. {law.group('ref_word').lower()} nr. {law.group('ref_no')} "
                f"af {normalize_whitespace(law.group('ref_date'))}"
            )
        elif law.group("law_no"):
            reference = number_key("lov", law.group("law_no"), law.group("law_year"))
            label = title
        else:
            reference = title_key
            label = title

        for section in sections or [None]:
            relations.append(
                ExtractedRelation(
                    relation_type=RelationType.EMPOWERED_BY,
                    target_reference=reference,
                    target_label=label,
                    specific_section=section,
                )
            )
    return relations


# -- Forgængere -------------------------------------------------------------

#: "Hermed bekendtgøres lov om X, jf. lovbekendtgørelse nr. N af DATO".
_CONSOLIDATION_RE = re.compile(
    r"Hermed bekendtgøres\s+lov\s+om\s+.+?,\s*jf\.\s*lovbekendtgørelse\s+nr\.\s*(?P<no>\d+)"
    r"\s+af\s+(?P<date>[^,]*?(?P<year>\d{4}))",
    re.IGNORECASE | re.DOTALL,
)

#: En henvisning til et andet dokument: "bekendtgørelse nr. 99 af 1. januar 2010".
_DOCUMENT_REF_RE = re.compile(
    r"(?P<word>lovbekendtgørelse|bekendtgørelse|lov|cirkulære|vejledning)\s+nr\.\s*(?P<no>\d+)"
    r"\s+af\s+(?P<date>\d{1,2}\.\s*[a-zæøå]+\s+(?P<year>\d{4})|\d{1,2}/\d{1,2}/(?P<year2>\d{4}))",
    re.IGNORECASE,
)

#: Ændringsforskrifter: "I bekendtgørelse nr. 101 … foretages følgende ændringer".
#: Deres "ophæves" gælder enkelte paragraffer, ikke hele dokumenter.
_AMENDMENT_RE = re.compile(r"foretages\s+følgende\s+ændring", re.IGNORECASE)


def _repealed_references(content: str) -> list[tuple[str, str]]:
    """(nøgle, visningstekst) for dokumenter, teksten ophæver."""
    text = content or ""
    if _AMENDMENT_RE.search(text):
        return []
    found: list[tuple[str, str]] = []
    # Ophævelser står på linjer/i sætninger med ordet "ophæves". Ophævelsesakter
    # opregner dem som punkter under en indledning, der slutter med "ophæves:".
    for line in text.splitlines():
        if "ophæve" not in line.lower():
            continue
        for ref in _DOCUMENT_REF_RE.finditer(line):
            year = ref.group("year") or ref.group("year2")
            found.append(
                (
                    number_key(ref.group("word"), ref.group("no"), year),
                    normalize_whitespace(ref.group(0)),
                )
            )
    # Ophævelsesakter: punkterne står ofte på egne linjer efter "ophæves:".
    lead = re.search(r"ophæves\s*:", text, re.IGNORECASE)
    if lead:
        for ref in _DOCUMENT_REF_RE.finditer(text[lead.end():]):
            year = ref.group("year") or ref.group("year2")
            item = (number_key(ref.group("word"), ref.group("no"), year), normalize_whitespace(ref.group(0)))
            if item not in found:
                found.append(item)
    return found


# -- Metadata ---------------------------------------------------------------

_METADATA_KEYS = {
    "erstatter": RelationType.REPLACES,
    "afloeser": RelationType.REPLACES,
    "replaces": RelationType.REPLACES,
    "predecessor": RelationType.REPLACES,
    "forgaenger": RelationType.REPLACES,
    "erstattetaf": RelationType.REPLACED_BY,
    "afloestaf": RelationType.REPLACED_BY,
    "replacedby": RelationType.REPLACED_BY,
    "successor": RelationType.REPLACED_BY,
    "efterfoelger": RelationType.REPLACED_BY,
    "ophaever": RelationType.REPEALS,
    "repeals": RelationType.REPEALS,
}
_ELI_RE = re.compile(r"eli/(?:lta|ft|retsinfo)/(?P<year>\d{4})/(?P<no>\d+)", re.IGNORECASE)
_ACCN_RE = re.compile(r"\b(?P<accn>[A-Z]\d{8,})\b")


def _metadata_relations(raw_metadata: dict | None) -> list[ExtractedRelation]:
    fields = (raw_metadata or {}).get("fields") or {}
    relations: list[ExtractedRelation] = []
    for key, value in fields.items():
        relation_type = _METADATA_KEYS.get(fold(str(key)).replace("_", "").replace("-", ""))
        if relation_type is None or not value:
            continue
        text = str(value)
        for eli in _ELI_RE.finditer(text):
            relations.append(ExtractedRelation(
                relation_type, f"{int(eli.group('no'))}/{eli.group('year')}", eli.group(0)
            ))
        for accn in _ACCN_RE.finditer(text):
            relations.append(ExtractedRelation(
                relation_type, f"accn:{accn.group('accn')}", accn.group("accn")
            ))
    return relations


# -- Samlet -----------------------------------------------------------------


def extract_relations(
    *,
    title: str | None,
    content: str | None,
    is_repeal_act: bool = False,
    raw_metadata: dict | None = None,
    own_keys: set[str] | None = None,
) -> list[ExtractedRelation]:
    """Alle relationer for ét dokument, uden dubletter og uden selvhenvisninger.

    ``own_keys`` er dokumentets egne nøgler ("BEK 101/2023" osv.); en tekst,
    der nævner sit eget nummer, må ikke blive sin egen forgænger.
    """
    content = content or ""
    relations: list[ExtractedRelation] = []

    relations.extend(extract_legal_basis(content))

    consolidation = _CONSOLIDATION_RE.search(content[:4000])
    if consolidation:
        relations.append(
            ExtractedRelation(
                RelationType.REPLACES,
                number_key("lovbekendtgørelse", consolidation.group("no"), consolidation.group("year")),
                f"lovbekendtgørelse nr. {consolidation.group('no')} af "
                f"{normalize_whitespace(consolidation.group('date'))}",
            )
        )

    relation_for_repealed = RelationType.REPEALS if is_repeal_act else RelationType.REPLACES
    for key, label in _repealed_references(content):
        relations.append(ExtractedRelation(relation_for_repealed, key, label))

    relations.extend(_metadata_relations(raw_metadata))

    own = own_keys or set()
    unique: list[ExtractedRelation] = []
    seen: set[tuple] = set()
    for relation in relations:
        identity = (relation.relation_type, relation.target_reference, relation.specific_section)
        if identity in seen or relation.target_reference in own:
            continue
        seen.add(identity)
        unique.append(relation)
    return unique

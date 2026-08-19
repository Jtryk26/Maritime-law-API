"""Tolerant parser for Retsinformations ELI-XML.

Dokumenter hentes fra https://www.retsinformation.dk/eli/accn/{accn}/xml.

SKEMAET ER NU DELVIST VERIFICERET
=================================
Skemaet er ikke formelt publiceret, men det er aflæst direkte fra
kildens egne svar (kontrolleret 18.08.2026 på accessionsnumrene
``A18650999930``, ``B19300001605`` og ``B20240123405``). Rodelementet er
``<Dokument>`` med disse børn:

    Meta, TitelGruppe, DokumentIndhold, UnderskriftGruppe, Bilag

og ``<Meta>`` bruger blandt andet felterne::

    DocumentType  Rank  AccessionNumber  DocumentId  UniqueDocumentId
    DocumentTitle  Year  DiesSigni  DateOfSubmit  StartDate  EndDate
    Status  Number  AnnouncedIn  DiesEdicti  DateOfHistoricMark
    Concerns  Change  Ref_Accn  Ref_Af  Ref_Text  Subject  Republished
    JournalNumber  Ministry  AdministrativeAuthority  EuReferences
    Signature  PlaceOfSignature

Det er navne som ``DiesSigni``, ``DiesEdicti``, ``StartDate``,
``AdministrativeAuthority`` og ``Number`` — ikke de danske navne man
kunne gætte sig til. Manglede de i `FIELD_CANDIDATES`, blev
``published_date``, ``effective_date`` og ``authority`` NULL, selv om
datoen stod i XML'en hele tiden. Det er den fejl, listen nedenfor retter.

TO NIVEAUER AF METADATA
=======================
Felter slås først op **inde i metadata-sektionen** og først derefter i
resten af dokumentet. Uden den afgrænsning ville et ``<Number>`` inde i
brødteksten kunne udkonkurrere ``<Meta><Number>``. Fald-tilbage til hele
dokumentet er bevaret, så et skema uden ``<Meta>`` stadig giver metadata.

DOKUMENTER UDEN BRØDTEKST
=========================
Ældre dokumenter leveres med **kun** ``<Meta>``. Tidligere faldt
udtrækningen tilbage til "al tekst i dokumentet" og gemte dermed
metadatateksten som om den var lovteksten. Det er nu udtrykkeligt et
tomt indhold plus ``content_kind = "metadata_only"``, så driften kan se
forskel på "vi mangler at hente teksten" og "teksten findes ikke hos
kilden". Se :mod:`app.services.legal.content_kind`.

Parseren er stadig bevidst defensiv:

  * Metadata søges via en prioriteret liste af kendte elementnavne,
    uafhængigt af namespace og af store/små bogstaver.
  * Findes et felt ikke, returneres None frem for at fejle.
  * Er inputtet slet ikke velformet XML, behandles det som HTML/ren tekst.

En skemaændring hos kilden skal give dårligere metadata, ikke et nedbrud
i importen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

from app.core.logging import get_logger
from app.core.text import normalize_whitespace, strip_html
from app.services.legal.content_kind import CONTENT_KIND_EMPTY, classify_content

logger = get_logger(__name__)

__all__ = ["ParsedDocumentXml", "parse_document_xml"]

# Kandidatelementnavne pr. logisk felt, i prioriteret rækkefølge.
# Sammenlignes uden namespace og uden hensyn til store/små bogstaver.
FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "title": ("documenttitle", "title", "titel", "dctitle", "langtitel", "officieltitel"),
    "short_title": ("shorttitle", "korttitel", "populartitle", "kaldenavn"),
    "document_type": ("documenttype", "dokumenttype", "type", "doktype", "shortname"),
    "authority": (
        "administrativeauthority",  # verificeret: <Meta><AdministrativeAuthority>
        "authority",
        "myndighed",
        "ressort",
        "ressortmyndighed",
        "udsteder",
        "publisher",
    ),
    "ministry": ("ministry", "ministerium", "ressortministerium"),
    # DiesSigni er dokumentets egen dato — den der står i titlen
    # ("BEK nr 1234 af 25/11/2024"), og den praktikere genkender.
    # DiesEdicti er kundgørelsesdatoen og bruges kun som reserve.
    "published_date": (
        "diessigni",
        "publicationdate", "publiceringsdato", "kundgoerelsesdato",
        "datepublished", "dato", "publiceret",
        "diesedicti",
    ),
    # StartDate er ikrafttrædelsen. Bemærk at der kan være flere, én pr.
    # ændringstrin; den første i dokumentorden vælges.
    "effective_date": (
        "effectivedate", "ikrafttraedelsesdato", "ikrafttraedelse",
        "gyldigfra", "validfrom", "startdate",
    ),
    "status": ("status", "retsinfostatus", "gyldighedsstatus", "documentstatus"),
    "document_number": (
        "documentnumber", "nummer", "lovnummer", "beknummer",
        "number", "accessionsnummer",
    ),
    "journal_number": ("journalnumber", "journalnummer", "jnr"),
}

# Elementer der afgrænser metadata-sektionen. Felter slås op her først.
META_CONTAINERS: frozenset[str] = frozenset({
    "meta", "metadata", "documentmetadata", "head", "header",
})

# Elementer der typisk indeholder selve lovteksten.
CONTENT_CANDIDATES: tuple[str, ...] = (
    "dokumentindhold", "documentcontents", "content", "indhold",
    "body", "brødtekst", "brodtekst", "text", "tekst", "documentbody",
)

# Sektioner der hører til dokumentet, men ikke er selve paragrafteksten.
# Bilag bærer ofte de tekniske krav — de må ikke tabes.
SUPPLEMENTARY_CANDIDATES: tuple[str, ...] = (
    "bilag", "bilagsgruppe", "appendix", "underskriftgruppe",
)

#: Alle sektioner der udgør dokumentets tekst, i den rækkefølge de står.
SECTION_CANDIDATES: frozenset[str] = frozenset(
    CONTENT_CANDIDATES + SUPPLEMENTARY_CANDIDATES
)

# Elementer der aldrig skal med i brødteksten.
CONTENT_EXCLUDE: frozenset[str] = META_CONTAINERS | frozenset({"script", "style"})

_KEYWORD_CANDIDATES: tuple[str, ...] = ("keyword", "noegleord", "emneord", "subject", "tag")

_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)


@dataclass(slots=True)
class ParsedDocumentXml:
    """Resultatet af at parse et kildedokument."""

    title: str | None = None
    short_title: str | None = None
    document_type: str | None = None
    authority: str | None = None
    ministry: str | None = None
    published_date: str | None = None
    effective_date: str | None = None
    status: str | None = None
    document_number: str | None = None
    journal_number: str | None = None
    keywords: list[str] = field(default_factory=list)
    content: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    parse_mode: str = "xml"
    #: Se :mod:`app.services.legal.content_kind`. Skelner "kilden har
    #: ingen tekst" fra "vi har tekst uden paragraffer".
    content_kind: str = CONTENT_KIND_EMPTY


def _local_name(tag: Any) -> str:
    """Elementnavn uden namespace, i små bogstaver."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


#: En linje der begynder med et lille bogstav eller et skilletegn er ikke
#: en ny bestemmelse — den er resten af den forrige sætning, delt af
#: inline-markup (<i>, <Ref>, <Sup> ...). Se `_element_lines`.
_CONTINUATION_RE = re.compile(r"^[a-zæøåéè0-9]|^[,.;:)\]»%]")
#: Slutter den forrige linje her, er sætningen færdig, og næste linje er
#: en ny enhed uanset hvordan den begynder.
_SENTENCE_END_RE = re.compile(r"[.!?:]$")


def _element_lines(element: ElementTree.Element) -> list[str]:
    """Teksten under et element, ÉN LINJE PR. ELEMENTGRÆNSE.

    Hvorfor det er afgørende
    ========================
    Kildens XML bærer selv dokumentets struktur: kapitler, paragraffer og
    stykker er hver sit element. Den tidligere udgave gjorde::

        normalize_whitespace(" ".join(element.itertext()))

    og `normalize_whitespace` klapper **alt** whitespace sammen — også
    linjeskift. Hele lovteksten kom derfor ud som én lang linje, og
    strukturparseren, hvis mønstre er forankret i linjestart, fandt
    hverken kapitler eller paragraffer. Resultatet var et indeks af
    vilkårlige tekstvinduer i stedet for paragraffer, uden at noget
    fejlede undervejs.

    Strukturen skal altså **bevares** her, ikke genskabes senere.

    Inline-markup
    =============
    Ikke enhver elementgrænse er en strukturgrænse: ``Skibet skal
    <i>altid</i> være sødygtigt`` er én sætning i tre noder. Linjer der
    begynder med lille bogstav eller skilletegn føjes derfor tilbage til
    den forrige linje, medmindre den forrige sluttede en sætning. En
    strukturmarkør begynder aldrig med lille bogstav — den begynder med
    ``§``, ``Kapitel``, ``Stk.`` eller et stort bogstav.
    """
    lines: list[str] = []

    def emit(value: str | None) -> None:
        text = normalize_whitespace(value or "")
        if not text:
            return
        if lines and _CONTINUATION_RE.match(text) and not _SENTENCE_END_RE.search(lines[-1]):
            lines[-1] = f"{lines[-1]} {text}"
        else:
            lines.append(text)

    def walk(node: ElementTree.Element) -> None:
        emit(node.text)
        for child in node:
            walk(child)
            # `tail` er tekst EFTER barnets slut-tag, altså fortsættelsen
            # af forælderens sætning. Den hører til den linje, barnet
            # sluttede på, og reglen ovenfor får den derhen.
            emit(child.tail)

    walk(element)
    return lines


def _element_text(element: ElementTree.Element) -> str:
    """Al tekst under et element, med elementgrænserne bevaret som linjeskift."""
    return "\n".join(_element_lines(element))


def _find_sections(
    root: ElementTree.Element, names: frozenset[str]
) -> list[ElementTree.Element]:
    """De YDERSTE elementer med et af navnene, i dokumentorden.

    Der gås ikke ned i et fund, så et ``<Bilag>`` inde i
    ``<DokumentIndhold>`` ikke tælles to gange.
    """
    if _local_name(root.tag) in names:
        return [root]

    found: list[ElementTree.Element] = []

    def walk(node: ElementTree.Element) -> None:
        for child in node:
            if _local_name(child.tag) in names:
                found.append(child)
            else:
                walk(child)

    walk(root)
    return found


def _collect_texts(*roots: ElementTree.Element) -> dict[str, list[str]]:
    """Bygger et opslag fra elementnavn til alle dets tekstværdier.

    Attributter medtages også, da metadata i nogle skemaer ligger som
    attributter frem for elementer.
    """
    found: dict[str, list[str]] = {}

    for element in (e for root in roots for e in root.iter()):
        name = _local_name(element.tag)
        if not name:
            continue

        direct = normalize_whitespace("".join(element.itertext()))
        if direct:
            found.setdefault(name, []).append(direct)

        for attr_name, attr_value in element.attrib.items():
            attr_key = _local_name(attr_name)
            value = normalize_whitespace(str(attr_value))
            if attr_key and value:
                found.setdefault(attr_key, []).append(value)

    return found


def _pick(texts: dict[str, list[str]], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        values = texts.get(candidate)
        if values:
            for value in values:
                if value:
                    return value
    return None


def _extract_body(root: ElementTree.Element) -> tuple[str, bool]:
    """Udtrækker brødteksten.

    Returnerer ``(tekst, kilden_havde_brødtekst)``. Det andet element er
    det vigtige: leverede kilden overhovedet et tekstelement? Er svaret
    nej, må metadatateksten IKKE gemmes som lovtekst — så er dokumentet
    ``metadata_only``, og en genimport ændrer ikke på det.
    """
    # Er hele svaret en metadata-sektion, er der pr. definition ingen tekst.
    if _local_name(root.tag) in META_CONTAINERS:
        return "", False

    sections = _find_sections(root, SECTION_CANDIDATES)
    if sections:
        text = "\n".join(t for section in sections if (t := _element_text(section)))
        if text:
            return text, True

    # Ukendt skema: alt der ikke er metadata regnes som tekst.
    parts: list[str] = []
    for child in root:
        if _local_name(child.tag) in CONTENT_EXCLUDE:
            continue
        text = _element_text(child)
        if text:
            parts.append(text)
    if parts:
        return "\n".join(parts), True

    # Ingen brugbare børn. Havde roden et metadata-element, er dokumentet
    # metadata-only; ellers er roden selv teksten.
    if any(_local_name(child.tag) in META_CONTAINERS for child in root):
        return "", False

    text = _element_text(root)
    return text, bool(text)


def parse_document_xml(payload: str) -> ParsedDocumentXml:
    """Parser et kildedokument. Kaster aldrig på uventet struktur."""
    if not payload or not payload.strip():
        return ParsedDocumentXml(parse_mode="empty")

    try:
        root = ElementTree.fromstring(_XML_DECL_RE.sub("", payload).strip())
    except ElementTree.ParseError as exc:
        # Ikke velformet XML — behandl som HTML/ren tekst, så importen
        # stadig får brugbart indhold ud af dokumentet.
        logger.warning("retsinformation.xml.parse_failed", extra={"error": str(exc)})
        text = strip_html(payload)
        first_line = text.split("\n", 1)[0] if text else None
        return ParsedDocumentXml(
            title=first_line,
            content=text,
            parse_mode="fallback-text",
            content_kind=classify_content(text),
            raw_metadata={"parse_error": str(exc)},
        )

    # To niveauer: metadata-sektionen har forrang, hele dokumentet er reserve.
    meta_sections = _find_sections(root, META_CONTAINERS)
    meta_texts = _collect_texts(*meta_sections) if meta_sections else {}
    texts = _collect_texts(root)

    def pick(field_name: str) -> str | None:
        candidates = FIELD_CANDIDATES[field_name]
        return _pick(meta_texts, candidates) or _pick(texts, candidates)

    content, source_had_body = _extract_body(root)

    keywords: list[str] = []
    for candidate in _KEYWORD_CANDIDATES:
        keywords.extend(texts.get(candidate, []))

    parsed = ParsedDocumentXml(
        title=pick("title"),
        short_title=pick("short_title"),
        document_type=pick("document_type"),
        authority=pick("authority"),
        ministry=pick("ministry"),
        published_date=pick("published_date"),
        effective_date=pick("effective_date"),
        status=pick("status"),
        document_number=pick("document_number"),
        journal_number=pick("journal_number"),
        keywords=sorted({k for k in keywords if k}),
        content=content,
        parse_mode="xml",
        content_kind=classify_content(content, source_had_body=source_had_body),
    )

    # Bevar et afgrænset uddrag af de rå felter til sporbarhed og fejlsøgning,
    # uden at gemme hele dokumentet igen.
    # Er der en metadata-sektion, gemmes kun den. Ellers ville hvert
    # elementnavn i brødteksten ende her og gøre sporet ulæseligt.
    source_fields = meta_texts or texts
    parsed.raw_metadata = {
        "root_tag": _local_name(root.tag),
        "content_kind": parsed.content_kind,
        "source_had_body": source_had_body,
        "fields": {
            key: values[0][:500]
            for key, values in sorted(source_fields.items())
            if values and len(values[0]) <= 500
        },
    }
    return parsed

"""Tolerant parser for Retsinformations ELI-XML.

Dokumenter hentes fra https://www.retsinformation.dk/eli/accn/{accn}/xml.

VIGTIGT OM DENNE PARSER
=======================
Det præcise XML-skema er ikke publiceret på en form der kunne verificeres
under udviklingen. Parseren er derfor bevidst defensiv:

  * Metadata søges via en prioriteret liste af kendte/sandsynlige
    elementnavne, uafhængigt af namespace og af store/små bogstaver.
  * Findes et felt ikke, returneres None frem for at fejle.
  * Brødteksten udtrækkes som al tekst under dokumentets tekstsektion,
    med fald tilbage til hele dokumentet.
  * Er inputtet slet ikke velformet XML, behandles det som HTML/ren tekst.

Denne strategi gør at en skemaændring hos kilden giver dårligere
metadata frem for et nedbrud i importen. Når skemaet er verificeret mod
produktion, bør `FIELD_CANDIDATES` strammes op.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

from app.core.logging import get_logger
from app.core.text import normalize_whitespace, strip_html

logger = get_logger(__name__)

__all__ = ["ParsedDocumentXml", "parse_document_xml"]

# Kandidatelementnavne pr. logisk felt, i prioriteret rækkefølge.
# Sammenlignes uden namespace og uden hensyn til store/små bogstaver.
FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "title": ("title", "titel", "dctitle", "documenttitle", "langtitel", "officieltitel"),
    "short_title": ("shorttitle", "korttitel", "populartitle", "kaldenavn"),
    "document_type": ("documenttype", "dokumenttype", "type", "doktype", "shortname"),
    "authority": ("authority", "myndighed", "ressort", "ressortmyndighed", "udsteder", "publisher"),
    "ministry": ("ministry", "ministerium", "ressortministerium"),
    "published_date": (
        "publicationdate", "publiceringsdato", "kundgoerelsesdato",
        "datepublished", "dato", "publiceret",
    ),
    "effective_date": (
        "effectivedate", "ikrafttraedelsesdato", "ikrafttraedelse",
        "gyldigfra", "validfrom",
    ),
    "status": ("status", "retsinfostatus", "gyldighedsstatus", "documentstatus"),
    "document_number": ("documentnumber", "nummer", "lovnummer", "beknummer", "accessionsnummer"),
}

# Elementer der typisk indeholder selve lovteksten.
CONTENT_CANDIDATES: tuple[str, ...] = (
    "documentcontents", "dokumentindhold", "content", "indhold",
    "body", "brødtekst", "brodtekst", "text", "tekst", "documentbody",
)

# Elementer der aldrig skal med i brødteksten.
CONTENT_EXCLUDE: frozenset[str] = frozenset({
    "metadata", "meta", "documentmetadata", "head", "header",
    "script", "style", "signature",
})

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
    keywords: list[str] = field(default_factory=list)
    content: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    parse_mode: str = "xml"


def _local_name(tag: Any) -> str:
    """Elementnavn uden namespace, i små bogstaver."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _element_text(element: ElementTree.Element) -> str:
    """Al tekst under et element, med mellemrum mellem noder."""
    parts = [t for t in element.itertext()]
    return normalize_whitespace(" ".join(parts))


def _collect_texts(root: ElementTree.Element) -> dict[str, list[str]]:
    """Bygger et opslag fra elementnavn til alle dets tekstværdier.

    Attributter medtages også, da metadata i nogle skemaer ligger som
    attributter frem for elementer.
    """
    found: dict[str, list[str]] = {}

    for element in root.iter():
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


def _extract_content(root: ElementTree.Element) -> str:
    """Udtrækker brødteksten.

    Foretrækker et kendt indholdselement. Findes intet, bruges hele
    dokumentet fraregnet metadata-sektioner.
    """
    for element in root.iter():
        if _local_name(element.tag) in CONTENT_CANDIDATES:
            text = _element_text(element)
            if len(text) > 40:
                return text

    parts: list[str] = []
    for child in root:
        if _local_name(child.tag) in CONTENT_EXCLUDE:
            continue
        text = _element_text(child)
        if text:
            parts.append(text)

    if parts:
        return "\n".join(parts)
    return _element_text(root)


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
            raw_metadata={"parse_error": str(exc)},
        )

    texts = _collect_texts(root)

    keywords: list[str] = []
    for candidate in _KEYWORD_CANDIDATES:
        keywords.extend(texts.get(candidate, []))

    parsed = ParsedDocumentXml(
        title=_pick(texts, FIELD_CANDIDATES["title"]),
        short_title=_pick(texts, FIELD_CANDIDATES["short_title"]),
        document_type=_pick(texts, FIELD_CANDIDATES["document_type"]),
        authority=_pick(texts, FIELD_CANDIDATES["authority"]),
        ministry=_pick(texts, FIELD_CANDIDATES["ministry"]),
        published_date=_pick(texts, FIELD_CANDIDATES["published_date"]),
        effective_date=_pick(texts, FIELD_CANDIDATES["effective_date"]),
        status=_pick(texts, FIELD_CANDIDATES["status"]),
        document_number=_pick(texts, FIELD_CANDIDATES["document_number"]),
        keywords=sorted({k for k in keywords if k}),
        content=_extract_content(root),
        parse_mode="xml",
    )

    # Bevar et afgrænset uddrag af de rå felter til sporbarhed og fejlsøgning,
    # uden at gemme hele dokumentet igen.
    parsed.raw_metadata = {
        "root_tag": _local_name(root.tag),
        "fields": {
            key: values[0][:500]
            for key, values in sorted(texts.items())
            if values and len(values[0]) <= 500
        },
    }
    return parsed

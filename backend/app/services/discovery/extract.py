"""Tolerant udtræk af kandidater fra et søgesvar.

HVORFOR DETTE MODUL ER SKREVET TOLERANT
=======================================
Retsinformations avancerede søgeside er en JavaScript-applikation. Dens
interne resultatendpoint er **ikke** en dokumenteret del af høsteservicen
(se ``docs/`` og modulet :mod:`app.services.discovery.search_client`), og
feltnavnene kan derfor ændre sig uden varsel.

Modulet gætter derfor ikke på ét bestemt skema. Det leder efter
*strukturen* — en liste af poster, hvor posterne indeholder noget der
ligner et accessionsnummer — og trækker de felter ud, der kan
genkendes. Manglende metadata er acceptabelt; et manglende
accessionsnummer er ikke, for så er posten ubrugelig for køen.

Samme princip som :mod:`app.services.retsinformation.xml_parser`, af
samme grund.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

from app.core.logging import get_logger
from app.services.retsinformation.normalization import parse_danish_date

logger = get_logger(__name__)

__all__ = [
    "ACCESSION_PATTERN",
    "extract_accession_number",
    "extract_hit_fields",
    "find_record_list",
    "find_reported_total",
    "describe_payload",
]

#: Retsinformations accessionsnumre er 1–2 bogstaver efterfulgt af cifre,
#: f.eks. ``B20220122005`` eller ``AA000012605``. Mønsteret bruges kun som
#: *bekræftelse* — findes der et felt der hedder noget med "accession",
#: bruges det uanset form.
ACCESSION_PATTERN = re.compile(r"^[A-ZÆØÅ]{1,2}[0-9]{6,14}$")

#: ELI-URL'er bærer nummeret direkte: /eli/accn/{accn}
_ELI_ACCN = re.compile(r"/eli/accn/([A-Za-z0-9ÆØÅæøå]+)", re.IGNORECASE)

_ACCESSION_KEYS = ("accessionsnummer", "accessionnumber", "accession", "accn")
_TITLE_KEYS = ("title", "titel", "documenttitle", "shorttitle", "korttitel", "name", "navn")
_AUTHORITY_KEYS = (
    "administrerendemyndighed",
    "ressortmyndighed",
    "myndighed",
    "authority",
    "ministerium",
    "ressort",
)
_STATUS_KEYS = (
    "retsinformationstatus",
    "gyldighedsstatus",
    "documentstate",
    "statustext",
    "status",
)
_TYPE_KEYS = ("documenttype", "doktype", "doctype", "dokumenttype", "typename", "type")
_DATE_KEYS = (
    "publicationdate",
    "publiceringsdato",
    "offentliggoerelsesdato",
    "offentliggørelsesdato",
    "datefordocument",
    "datofordokument",
    "documentdate",
    "dokumentdato",
    "published",
    "date",
)
_URL_KEYS = ("eliurl", "eli", "href", "url", "link", "documenturl")

_TOTAL_KEYS = (
    "totalcount",
    "totalhits",
    "totalresults",
    "totalnumberofresults",
    "total",
    "antalresultater",
    "antal",
    "hitcount",
    "resultcount",
    "count",
    "numberofresults",
)

#: Beskyttelse mod et uventet stort svar: hvor dybt vi leder efter lister.
_MAX_DEPTH = 8


def _normalize_key(key: str) -> str:
    """Gør feltnavne sammenlignelige: ``Total Count`` → ``totalcount``."""
    return re.sub(r"[^a-z0-9æøå]", "", str(key).lower())


def _scalar(value: Any) -> str | None:
    """Presser en værdi ned til en streng.

    Kilder pakker ofte værdier i objekter (``documentType: {shortName: ...}``)
    eller lister. Vi tager den første brugbare tekst.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for candidate in ("shortname", "name", "value", "title", "text", "navn"):
            for key, inner in value.items():
                if _normalize_key(key) == candidate:
                    text = _scalar(inner)
                    if text:
                        return text
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _scalar(item)
            if text:
                return text
    return None


def _first_by_keys(record: dict[str, Any], keys: Iterable[str]) -> str | None:
    """Første felt hvis normaliserede navn matcher — eksakt før delvist."""
    normalized = {_normalize_key(k): v for k, v in record.items()}

    for key in keys:
        if key in normalized:
            text = _scalar(normalized[key])
            if text:
                return text

    for key in keys:
        for name, value in normalized.items():
            if key in name:
                text = _scalar(value)
                if text:
                    return text
    return None


def extract_accession_number(record: dict[str, Any]) -> str | None:
    """Finder postens accessionsnummer.

    Tre veje, i faldende tillid:

    1. Et felt der hedder noget med "accession".
    2. En ELI-URL i et vilkårligt strengfelt (``/eli/accn/{accn}``).
    3. Et vilkårligt strengfelt hvis værdi *ligner* et accessionsnummer.

    Punkt 3 er sidste udvej og accepterer kun mønsteret i
    :data:`ACCESSION_PATTERN`, så almindelige numeriske id'er ikke
    forveksles med accessionsnumre.
    """
    if not isinstance(record, dict):
        return None

    direct = _first_by_keys(record, _ACCESSION_KEYS)
    if direct:
        return direct.strip()

    for value in record.values():
        text = _scalar(value)
        if text:
            match = _ELI_ACCN.search(text)
            if match:
                return match.group(1)

    for key, value in record.items():
        if _normalize_key(key) in {"id", "documentid", "dokumentid", "docid"}:
            text = _scalar(value)
            if text and ACCESSION_PATTERN.match(text.upper()):
                return text.strip()

    for value in record.values():
        text = _scalar(value)
        if text and ACCESSION_PATTERN.match(text.upper()):
            return text.strip()

    return None


def _extract_date(record: dict[str, Any]) -> date | None:
    raw = _first_by_keys(record, _DATE_KEYS)
    if not raw:
        return None
    parsed = parse_danish_date(raw)
    if parsed:
        return parsed
    # ISO-8601 med tidsstempel: 2024-03-05T00:00:00+01:00
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _extract_url(record: dict[str, Any]) -> str | None:
    """Foretrækker en ELI-URL; ellers første URL-lignende felt."""
    for value in record.values():
        text = _scalar(value)
        if text and _ELI_ACCN.search(text) and text.lower().startswith("http"):
            return text

    candidate = _first_by_keys(record, _URL_KEYS)
    if candidate and candidate.lower().startswith("http"):
        return candidate
    return None


def extract_hit_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Trækker de felter ud, CSV-manifestet bruger.

    Returnerer altid alle nøgler; ukendte felter er ``None``.
    """
    return {
        "accession_number": extract_accession_number(record),
        "title": _first_by_keys(record, _TITLE_KEYS),
        "authority": _first_by_keys(record, _AUTHORITY_KEYS),
        "status": _first_by_keys(record, _STATUS_KEYS),
        "document_type": _first_by_keys(record, _TYPE_KEYS),
        "published_date": _extract_date(record),
        "eli_url": _extract_url(record),
    }


def _iter_nodes(payload: Any, depth: int = 0):
    """Bredde-først gennemløb af JSON-træet."""
    if depth > _MAX_DEPTH:
        return
    yield payload
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _iter_nodes(value, depth + 1)
    elif isinstance(payload, list):
        for value in payload:
            yield from _iter_nodes(value, depth + 1)


def find_record_list(payload: Any) -> list[dict[str, Any]]:
    """Finder den liste i svaret, der indeholder søgeresultaterne.

    Kriteriet er ikke feltnavnet — det varierer (``documents``, ``items``,
    ``results``, ``hits``) — men indholdet: en liste af objekter, hvor
    mindst halvdelen har et genkendeligt accessionsnummer. Er der flere
    kandidater, vinder den længste.
    """
    best: list[dict[str, Any]] = []

    for node in _iter_nodes(payload):
        if not isinstance(node, list) or not node:
            continue
        records = [item for item in node if isinstance(item, dict)]
        if not records:
            continue
        with_accession = sum(1 for item in records if extract_accession_number(item))
        if with_accession * 2 < len(records):
            continue
        if len(records) > len(best):
            best = records

    return best


def find_reported_total(payload: Any) -> int | None:
    """Finder kildens eget resultattal, hvis svaret bærer et.

    Bruges til at kontrollere at pagineringen faktisk hentede alt. Kun
    felter på øverste niveau eller i et objekt tages med — et ``count``
    dybt nede i en enkelt post er ikke resultattallet.
    """
    candidates: list[tuple[int, int]] = []

    def scan(node: Any, depth: int) -> None:
        if depth > 3 or not isinstance(node, dict):
            return
        for key, value in node.items():
            normalized = _normalize_key(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and normalized in _TOTAL_KEYS:
                rank = _TOTAL_KEYS.index(normalized)
                candidates.append((rank, value))
            elif isinstance(value, dict):
                scan(value, depth + 1)

    scan(payload, 0)
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def describe_payload(payload: Any) -> dict[str, Any]:
    """Struktur-rapport til ``backfill probe-search``.

    Formålet er at et menneske på få linjer kan afgøre, om svaret
    indeholder accessionsnumre, hvordan paginering ser ud, og om
    formatet er stabilt nok til at bygge videre på.
    """
    records = find_record_list(payload)
    sample = records[0] if records else None

    pagination_keys: list[str] = []
    if isinstance(payload, dict):
        for key in payload:
            if _normalize_key(key) in {
                "page",
                "pagenumber",
                "pagesize",
                "pagecount",
                "totalpages",
                "skip",
                "take",
                "offset",
                "limit",
                "from",
                "size",
                "hasmore",
                "nextpage",
            }:
                pagination_keys.append(str(key))

    return {
        "toplevel_type": type(payload).__name__,
        "toplevel_keys": sorted(payload.keys()) if isinstance(payload, dict) else None,
        "records_found": len(records),
        "reported_total": find_reported_total(payload),
        "pagination_keys": sorted(pagination_keys),
        "record_keys": sorted(sample.keys()) if isinstance(sample, dict) else None,
        "sample_extraction": extract_hit_fields(sample) if sample else None,
        "accession_numbers": [
            extract_accession_number(item) for item in records[:5]
        ],
    }

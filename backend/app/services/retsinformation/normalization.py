"""Normalisering af kildespecifikke værdier til systemets interne form.

Holder oversættelsen af Retsinformations forkortelser, datoformater og
statusbetegnelser ét sted, så resten af systemet arbejder med
ensartede værdier.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.core.text import fold, normalize_whitespace

__all__ = [
    "map_document_type",
    "map_status",
    "parse_danish_date",
    "resolve_authority",
    "DOCUMENT_TYPES",
]

# Retsinformations dokumenttypeforkortelser. Suffikset "H" betegner
# den historiske/konsoliderede variant og udelades i den normaliserede form.
# Kilde: shortName-værdier observeret i høsteservicens dokumentation.
DOCUMENT_TYPES: dict[str, str] = {
    "LOV": "Lov",
    "LBK": "Lovbekendtgørelse",
    "BEK": "Bekendtgørelse",
    "CIR": "Cirkulære",
    "CIS": "Cirkulæreskrivelse",
    "VEJ": "Vejledning",
    "SKR": "Skrivelse",
    "AFG": "Afgørelse",
    "ANO": "Anordning",
    "ANG": "Anordning",
    "RES": "Resolution",
    "MED": "Meddelelse",
    "REG": "Regulativ",
    "FTB": "Folketingsbeslutning",
    "AND": "Andet",
}

_STATUS_MAP: dict[str, str] = {
    "valid": "Gældende",
    "gaeldende": "Gældende",
    "historisk": "Historisk",
    "historical": "Historisk",
    "ophaevet": "Ophævet",
    "repealed": "Ophævet",
    "fremtidig": "Fremtidig",
    "future": "Fremtidig",
}

_DATE_PATTERNS = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
)

_DANISH_MONTHS = {
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12,
}

_DANISH_DATE_RE = re.compile(
    r"(\d{1,2})\.?\s+(" + "|".join(_DANISH_MONTHS) + r")\s+(\d{4})",
    re.IGNORECASE,
)

# Myndigheder der kan udledes af dokumentets indhold, når kilden ikke
# angiver et eksplicit ressortfelt.
_AUTHORITY_HINTS: tuple[tuple[str, str], ...] = (
    ("soefartsstyrelsen", "Søfartsstyrelsen"),
    ("erhvervsministeriet", "Erhvervsministeriet"),
    ("miljoeministeriet", "Miljøministeriet"),
    ("miljoestyrelsen", "Miljøstyrelsen"),
    ("forsvarsministeriet", "Forsvarsministeriet"),
    ("justitsministeriet", "Justitsministeriet"),
    ("transportministeriet", "Transportministeriet"),
    ("beskaeftigelsesministeriet", "Beskæftigelsesministeriet"),
    ("fiskeristyrelsen", "Fiskeristyrelsen"),
    ("trafikstyrelsen", "Trafikstyrelsen"),
    ("undervisningsministeriet", "Undervisningsministeriet"),
    ("kulturministeriet", "Kulturministeriet"),
    ("socialministeriet", "Socialministeriet"),
)


def map_document_type(raw: str | None) -> str | None:
    """Oversætter en dokumenttypeforkortelse til læsbart dansk.

    >>> map_document_type("BEK H")
    'Bekendtgørelse'
    >>> map_document_type("Bekendtgørelse")
    'Bekendtgørelse'
    """
    if not raw:
        return None
    cleaned = normalize_whitespace(str(raw))
    if not cleaned:
        return None

    # "BEK H" -> "BEK". Suffikset markerer en historisk variant.
    code = cleaned.split()[0].upper().strip(".")
    if code in DOCUMENT_TYPES:
        return DOCUMENT_TYPES[code]

    # Allerede en læsbar betegnelse.
    return cleaned


def map_status(raw: str | None) -> str | None:
    """Normaliserer statusbetegnelse til dansk."""
    if not raw:
        return None
    cleaned = normalize_whitespace(str(raw))
    if not cleaned:
        return None
    return _STATUS_MAP.get(fold(cleaned), cleaned)


def parse_danish_date(raw: Any) -> date | None:
    """Fortolker de datoformater der optræder hos kilden.

    Håndterer ISO-datoer, danske talformater og skrevne datoer som
    "30. september 2024".
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw

    text = normalize_whitespace(str(raw))
    if not text:
        return None

    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue

    # ISO med tidszone-offset.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    match = _DANISH_DATE_RE.search(text)
    if match:
        day, month_name, year = match.groups()
        month = _DANISH_MONTHS[month_name.lower()]
        try:
            return date(int(year), month, int(day))
        except ValueError:
            return None

    return None


def resolve_authority(
    raw: str | None,
    *,
    title: str | None = None,
    content: str | None = None,
) -> str | None:
    """Fastlægger udstedende myndighed.

    Bruger kildens eget felt hvis det findes. Ellers udledes myndigheden
    af titel og de første afsnit af teksten, hvor udstederen typisk
    fremgår af kundgørelsesformlen.
    """
    if raw:
        cleaned = normalize_whitespace(str(raw))
        if cleaned:
            return cleaned

    haystack = fold(" ".join(filter(None, [title or "", (content or "")[:4000]])))
    for needle, label in _AUTHORITY_HINTS:
        if needle in haystack:
            return label
    return None

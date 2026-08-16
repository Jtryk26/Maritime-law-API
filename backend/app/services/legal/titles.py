"""Normaliserede visningstitler.

Problemet
=========
Officielle titler er skrevet for at være juridisk entydige, ikke for at
kunne skimmes:

.. code-block:: text

    Bekendtgørelse af lov om sikkerhed til søs (søsikkerhedsloven), jf.
    lovbekendtgørelse nr. 1629 af 17. december 2018 med senere ændringer

I en resultatliste, et kort på forsiden eller et panel med relaterede
dokumenter fylder sådan en titel tre linjer og siger stadig kun "sikkerhed
til søs".

Løsningen
=========
Hvert dokument bærer to titler:

``original_title``
    Uændret, juridisk korrekt. Vises i metadata, tooltip og fold-ud, og
    er den der citeres.

``display_title``
    Kort og læsbar. Bruges i søgeresultater, relaterede dokumenter, kort
    og dokumentheader.

Reglerne nedenfor er bevidst få og forsigtige. Hellere en titel der er
lidt for lang, end en der har mistet det ord, som adskiller den fra
naboparagraffen. Derfor forkortes der aldrig under
``MIN_MEANINGFUL_CHARS``, og der klippes kun ved grænser sproget selv
sætter — komma, "jf.", "samt", en parentes — aldrig midt i et ord uden
først at have prøvet alt andet.
"""

from __future__ import annotations

import re

from app.core.text import normalize_whitespace

__all__ = ["derive_display_title", "split_leading_type", "DEFAULT_MAX_CHARS"]

#: Over denne længde forsøges titlen afkortet.
DEFAULT_MAX_CHARS = 78
#: Under denne længde forkortes aldrig — så er der intet at vinde.
MIN_MEANINGFUL_CHARS = 24

#: Ledende dokumenttypebetegnelser, længste først så "Bekendtgørelse af lov om"
#: prøves før "Bekendtgørelse".
_LEADING_TYPES = (
    "Bekendtgørelse af lov om",
    "Bekendtgørelse af lov",
    "Anordning om ikrafttræden af lov om",
    "Lovbekendtgørelse om",
    "Bekendtgørelse om",
    "Bekendtgørelse af",
    "Cirkulæreskrivelse om",
    "Cirkulære om",
    "Vejledning om",
    "Anordning om",
    "Skrivelse om",
    "Meddelelse om",
    "Lov om",
)

#: Halesegmenter der aldrig bærer emnet. Klippes uanset længde.
_TAIL_PATTERNS = (
    re.compile(r",?\s*jf\.\s.*$", re.IGNORECASE),
    re.compile(r",?\s*med senere ændringer.*$", re.IGNORECASE),
    re.compile(r",?\s*som ændret ved.*$", re.IGNORECASE),
)

#: Grænser hvor en lang titel kan klippes uden at ødelægge sætningen.
_SOFT_BOUNDARIES = (
    " samt ",
    ", herunder ",
    ", og ",
    " og om ",
    " og for ",
    ", jf. ",
    ", ",
)

#: Fodnotehenvisninger i kundgjorte titler: "…fra skibe1)".
_FOOTNOTE_RE = re.compile(r"\s*\d+\)\s*$")
#: Afsluttende parentes: "(søsikkerhedsloven)".
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")


def split_leading_type(title: str) -> tuple[str | None, str]:
    """Deler titlen i dens ledende typebetegnelse og resten.

    >>> split_leading_type("Bekendtgørelse om redningsmidler i handelsskibe")
    ('Bekendtgørelse om', 'redningsmidler i handelsskibe')
    >>> split_leading_type("Søloven")
    (None, 'Søloven')
    """
    cleaned = normalize_whitespace(title or "")
    lowered = cleaned.lower()
    for prefix in _LEADING_TYPES:
        if lowered.startswith(prefix.lower()):
            return prefix, cleaned[len(prefix):].strip()
    return None, cleaned


def _capitalize_first(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def _strip_tails(title: str) -> str:
    result = title
    for pattern in _TAIL_PATTERNS:
        result = pattern.sub("", result)
    result = _FOOTNOTE_RE.sub("", result)
    return result.strip(" ,;–-")


def _shorten(title: str, max_chars: int) -> str:
    """Klipper en for lang titel ved en sproglig grænse.

    Rækkefølgen er: sproglige grænser (komma, "samt", "jf.") først, og
    kun hvis ingen af dem findes inden for grænsen, et ordskift. Et
    hårdt snit midt i et ord forekommer aldrig.
    """
    if len(title) <= max_chars:
        return title

    best = -1
    lowered = title.lower()
    for boundary in _SOFT_BOUNDARIES:
        position = lowered.rfind(boundary, MIN_MEANINGFUL_CHARS, max_chars)
        if position > best:
            best = position
    if best >= MIN_MEANINGFUL_CHARS:
        return title[:best].rstrip(" ,;–-")

    space = title.rfind(" ", MIN_MEANINGFUL_CHARS, max_chars)
    if space >= MIN_MEANINGFUL_CHARS:
        return title[:space].rstrip(" ,;–-") + "…"
    return title


def derive_display_title(
    title: str | None,
    *,
    short_title: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Danner den korte visningstitel.

    ``short_title`` fra kilden vinder, hvis den findes og er kortere —
    kilden har da selv angivet en populærtitel ("søloven"), og den er
    bedre end noget vi kan udlede.

    >>> derive_display_title("Bekendtgørelse af lov om sikkerhed til søs (søsikkerhedsloven)")
    'Lov om sikkerhed til søs'
    >>> derive_display_title("Bekendtgørelse om brandsikkerhed i passagerskibe")
    'Bekendtgørelse om brandsikkerhed i passagerskibe'
    """
    original = normalize_whitespace(title or "")
    if not original:
        return ""

    candidate = _strip_tails(original)

    # "Bekendtgørelse af lov om X" er i praksis loven X. Typebetegnelsen
    # "lovbekendtgørelse" er en kundgørelsesform, ikke et emne.
    prefix, rest = split_leading_type(candidate)
    if prefix and prefix.lower().startswith("bekendtgørelse af lov"):
        connector = "Lov om" if prefix.lower().endswith("om") else "Lov"
        candidate = normalize_whitespace(f"{connector} {rest}")
    elif prefix and prefix.lower() == "bekendtgørelse af" and rest:
        # "Bekendtgørelse af søloven" -> "Søloven".
        candidate = _capitalize_first(rest)

    # Afsluttende populærnavn i parentes fjernes, når titlen alligevel er
    # lang: "(søsikkerhedsloven)" gentager blot emnet.
    if len(candidate) > MIN_MEANINGFUL_CHARS:
        stripped = _TRAILING_PAREN_RE.sub("", candidate).strip()
        if len(stripped) >= MIN_MEANINGFUL_CHARS:
            candidate = stripped

    shortened = _shorten(candidate, max_chars)

    # Kildens egen korttitel er en nødudgang, ikke en forbedring.
    #
    # Retsinformations `short_title` er populærtitlen ("Søsikkerhedsloven"),
    # og den er ofte god. Men den er også et andet navn end det, brugeren
    # ser i resultatlisten fra en søgning på den fulde titel, og et skift
    # af navn koster genkendelighed. Den bruges derfor kun, når titlen
    # ikke kunne forkortes uden at blive klippet over — dér er en rigtig
    # populærtitel bedre end en afkortet formel titel.
    source_short = normalize_whitespace(short_title or "")
    if shortened != candidate and source_short and len(source_short) < len(shortened):
        return _capitalize_first(source_short)

    return shortened or original

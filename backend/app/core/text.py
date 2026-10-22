"""Tekstnormalisering, foldning og hashing.

Ét sted for al tekstbehandling, så relevansmotor, kategorisering,
versionshashing og søgning behandler tekst ens.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from bisect import bisect_right
from functools import lru_cache
from itertools import accumulate

__all__ = [
    "fold",
    "normalize_whitespace",
    "normalize_for_hash",
    "content_hash",
    "strip_html",
    "contains_any_term",
    "make_snippet",
    "tokenize",
]

# Dansk translitteration. Anvendes på både konfigurationstermer og dokumenttekst,
# så matchning er uafhængig af om kilden skriver "ø" eller "oe".
_FOLD_MAP = {
    "æ": "ae", "Æ": "ae",
    "ø": "oe", "Ø": "oe",
    "å": "aa", "Å": "aa",
    "ä": "ae", "Ä": "ae",
    "ö": "oe", "Ö": "oe",
    "ü": "ue", "Ü": "ue",
    "é": "e", "É": "e",
    "è": "e", "È": "e",
    "ô": "o", "Ô": "o",
    "\u00ad": "",   # soft hyphen — optræder i kundgjorte tekster
    "\u2019": "'",  # typografisk apostrof
    "\u2018": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u00a0": " ",  # non-breaking space
}

_FOLD_TABLE = str.maketrans(_FOLD_MAP)

#: Tegn før og efter et fund, der foldes, når en lang tekst gennemsøges med
#: ``scan_limit``. Rigeligt til et uddrag på ``max_length`` med ordgrænser.
_SNIPPET_WINDOW_RADIUS = 1_000


@lru_cache(maxsize=4096)
def _fold_char(char: str) -> str:
    """Ét tegns foldede form. Kan være længere ("æ" → "ae") eller tom (blød bindestreg)."""
    return char.translate(_FOLD_TABLE).lower()


def _fold_with_origin(text: str) -> tuple[str, list[int]]:
    """Foldet tekst og, for hvert foldet tegn, hvor mange kildetegn der ligger før.

    ``fold`` bevarer ikke længden: "æ" bliver "ae", en blød bindestreg
    forsvinder. En position fundet i den foldede tekst kan derfor ikke
    bruges direkte i kildeteksten. Her foldes tegn for tegn med *samme*
    oversættelse som ``fold``, og sammenhængen mellem de to positioner
    gemmes undervejs — korrekt for ethvert tegn, ``_FOLD_MAP`` eller
    ``str.lower`` ændrer længden på, uden en liste over særtilfælde.

    ``text`` skal være NFC-normaliseret, så ``fold``'s egen normalisering
    ikke ændrer noget. Prisen er lineær i ``len(text)`` i Python og bruges
    derfor kun på korte tekster: paragraffer og et afgrænset vindue.
    """
    pieces = [_fold_char(char) for char in text]
    return "".join(pieces), list(accumulate(map(len, pieces)))


def _source_position(cumulative: list[int], folded_position: int) -> int:
    """Kildeposition for en position i den foldede tekst. Se `_fold_with_origin`."""
    return bisect_right(cumulative, folded_position)


def _raw_alternatives() -> dict[str, list[str]]:
    """Foldet stykke → de rå tegn, der folder til det ("oe" → ["ø", "Ø", "ö", "Ö"])."""
    sources: dict[str, list[str]] = {}
    for raw, folded in _FOLD_MAP.items():
        if folded:
            sources.setdefault(folded, []).append(raw)
    return sources


_RAW_SOURCES = _raw_alternatives()
_SOFT_HYPHENS = "\u00ad*"  # foldes til "" og kan stå hvor som helst i et ord
_MAX_PATTERN_LENGTH = 4000


@lru_cache(maxsize=512)
def _raw_term_pattern(folded_term: str) -> re.Pattern[str]:
    """Regex, der finder ``folded_term`` i *ufoldet* tekst.

    Den matcher enhver rå stavning, der folder til termen: "soefart"
    matcher både "søfart", "Søfart", "soefart" og "sø\u00adfart". Mønstret
    afledes af ``_FOLD_MAP`` — samme kilde som ``fold`` — så de to ikke kan
    komme ud af trit. Søgningen kører i C over den rå tekst; kun et lille
    vindue omkring fundet skal derefter foldes.

    Et foldet tegnpar som "ae" kan komme fra "æ" *eller* fra "a" og "e", så
    mønstret forgrener sig ved hvert sådant par. For en sjælden, patologisk
    term ("aaaaaaaa…") ville det vokse eksponentielt; over en grænse bruges
    i stedet kun de direkte stavninger.
    """

    def unit(chunk: str) -> str:
        options = [re.escape(chunk)] + [re.escape(raw) for raw in _RAW_SOURCES.get(chunk, [])]
        return options[0] if len(options) == 1 else "(?:" + "|".join(options) + ")"

    memo: dict[int, str] = {}

    def from_position(i: int) -> str:
        if i >= len(folded_term):
            return ""
        if i not in memo:
            branches = [unit(folded_term[i]) + _SOFT_HYPHENS + from_position(i + 1)]
            pair = folded_term[i : i + 2]
            if len(pair) == 2 and pair in _RAW_SOURCES:
                branches.append(unit_pair(pair) + _SOFT_HYPHENS + from_position(i + 2))
            memo[i] = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return memo[i]

    def unit_pair(pair: str) -> str:
        return "(?:" + "|".join(re.escape(raw) for raw in _RAW_SOURCES[pair]) + ")"

    pattern = from_position(0)
    if len(pattern) > _MAX_PATTERN_LENGTH:
        pattern = _SOFT_HYPHENS.join(unit(char) for char in folded_term)
    return re.compile(pattern, re.IGNORECASE)


def contains_any_term(text: str, terms: list[str]) -> bool:
    """Står mindst ét af søgeordene i ``text``? Til korte tekster: uddrag og titler."""
    folded = fold(text)
    return any(ft and ft in folded for ft in (fold(term).strip() for term in terms))


def _first_raw_match(content: str, folded_terms: list[str]) -> int:
    """Kildeposition for det første søgeord i ufoldet tekst, eller -1."""
    first = -1
    for folded_term in folded_terms:
        match = _raw_term_pattern(folded_term).search(content)
        if match and (first == -1 or match.start() < first):
            first = match.start()
    return first

_WHITESPACE_RE = re.compile(r"\s+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_BLOCK_RE = re.compile(
    r"</?(p|div|br|tr|li|h[1-6]|table|section|article)\b[^>]*>",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[0-9a-zæøåäöüéè]+", re.IGNORECASE)


def fold(text: str) -> str:
    """Folder tekst til en matchvenlig form: NFC, translitteret, små bogstaver.

    >>> fold("Søfartsstyrelsen")
    'soefartsstyrelsen'
    >>> fold("SØFART") == fold("søfart")
    True
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    return text.translate(_FOLD_TABLE).lower()


def normalize_whitespace(text: str) -> str:
    """Kollapser alt whitespace til enkelte mellemrum og trimmer."""
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip()


def strip_html(text: str) -> str:
    """Fjerner HTML/XML-tags og bevarer læsbar afsnitsstruktur.

    Retsinformation leverer dokumenter som XML med indlejret markup.
    Blokelementer bliver til linjeskift, så teksten forbliver læsbar.
    """
    if not text:
        return ""
    text = _HTML_BLOCK_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    # Kollaps mellemrum pr. linje, fjern tomme linjer.
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def normalize_for_hash(text: str) -> str:
    """Kanonisk form der hashes.

    Bevarer store/små bogstaver — juridisk tekst er betydningsbærende —
    men ignorerer whitespace-forskelle og Unicode-varianter, så
    ren omformatering hos kilden ikke fremstår som en indholdsændring.
    """
    if not text:
        return ""
    return normalize_whitespace(unicodedata.normalize("NFC", text))


def content_hash(text: str) -> str:
    """SHA-256 over normaliseret indhold. Deterministisk på tværs af kørsler."""
    canonical = normalize_for_hash(text)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    """Splitter foldet tekst i søgetokens."""
    return _TOKEN_RE.findall(fold(text))


def make_snippet(
    content: str,
    terms: list[str],
    *,
    max_length: int = 320,
    scan_limit: int | None = None,
) -> str:
    """Uddrager et relevant tekstuddrag omkring første forekomst af et søgeord.

    Falder tilbage til dokumentets begyndelse hvis intet term findes.

    ``scan_limit`` gør uddraget billigt for lange tekster. Er ``content``
    længere, foldes og normaliseres ikke hele teksten: søgeordets første
    forekomst findes med et regex direkte i den rå tekst (se
    `_raw_term_pattern`, kører i C), og kun et vindue omkring fundet
    behandles. Findes intet søgeord — fx når fuldtekstsøgningen matchede en
    bøjningsform — bruges tekstens første ``scan_limit`` tegn.
    """
    if not content:
        return ""

    folded_terms = [ft for ft in (fold(term).strip() for term in terms) if ft]

    cut_before = cut_after = False
    if scan_limit is not None and len(content) > scan_limit:
        if not unicodedata.is_normalized("NFC", content):
            content = unicodedata.normalize("NFC", content)
        hit = _first_raw_match(content, folded_terms) if folded_terms else -1
        if hit == -1:
            window_start, window_end = 0, scan_limit
        else:
            radius = max(_SNIPPET_WINDOW_RADIUS, 2 * max_length)
            window_start, window_end = max(0, hit - radius), hit + radius
        cut_before = window_start > 0
        cut_after = window_end < len(content)
        content = content[window_start:window_end]

    flat = unicodedata.normalize("NFC", normalize_whitespace(content))
    if not flat:
        return ""

    # Er der ingen brugbare søgeord, er der intet at finde, og uddraget er
    # dokumentets begyndelse — så springes foldningen over.
    position = -1
    if folded_terms:
        folded, cumulative = _fold_with_origin(flat)
        for folded_term in folded_terms:
            found = folded.find(folded_term)
            if found != -1 and (position == -1 or found < position):
                position = found
        if position != -1:
            # Positionen er fundet i den foldede tekst; uddraget klippes af
            # kildeteksten. Uden omregning rykker vinduet ét tegn for hvert
            # "æ", "ø" og "å" før fundet — dybt i en lov forbi selve fundet.
            position = _source_position(cumulative, position)

    if position == -1:
        snippet = flat[:max_length]
        prefix = "…" if cut_before else ""
        return prefix + snippet + ("…" if len(flat) > max_length or cut_after else "")

    # Centrér uddraget omkring fundet og klip ved ordgrænser.
    half = max_length // 2
    start = max(0, position - half)
    end = min(len(flat), start + max_length)

    if start > 0:
        space = flat.find(" ", start)
        start = space + 1 if space != -1 and space < position else start
    if end < len(flat):
        space = flat.rfind(" ", start, end)
        end = space if space != -1 and space > position else end

    snippet = flat[start:end].strip()
    prefix = "…" if start > 0 or cut_before else ""
    suffix = "…" if end < len(flat) or cut_after else ""
    return f"{prefix}{snippet}{suffix}"

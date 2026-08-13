"""Tekstnormalisering, foldning og hashing.

Ét sted for al tekstbehandling, så relevansmotor, kategorisering,
versionshashing og søgning behandler tekst ens.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

__all__ = [
    "fold",
    "normalize_whitespace",
    "normalize_for_hash",
    "content_hash",
    "strip_html",
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
) -> str:
    """Uddrager et relevant tekstuddrag omkring første forekomst af et søgeord.

    Falder tilbage til dokumentets begyndelse hvis intet term findes.
    """
    if not content:
        return ""

    flat = normalize_whitespace(content)
    if not flat:
        return ""

    folded = fold(flat)
    position = -1
    for term in terms:
        folded_term = fold(term).strip()
        if not folded_term:
            continue
        found = folded.find(folded_term)
        if found != -1 and (position == -1 or found < position):
            position = found

    if position == -1:
        snippet = flat[:max_length]
        return snippet + ("…" if len(flat) > max_length else "")

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
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return f"{prefix}{snippet}{suffix}"

"""Regelbaseret klassifikation af et geografisk fund.

Et fund er først og fremmest en *omtale*. Det bliver kun til andet, når
sætningen omkring det bærer en sproglig markør:

=============  ============================================================
APPLIES        "gælder for", "finder anvendelse", "applies to", "within",
               "sejlads i" — eller en forpligtelse ("skal", "shall") med
               et stedsforholdsord ("i", "in") lige før navnet.
EXEMPTS        "gælder ikke", "undtagen", "excluding", "shall not apply".
DEFINES        "forstås", "means", "is bounded by", "designated as".
HISTORICAL     "ophævet", "repealed", "no longer" — eller dokumentet selv
               er historisk.
MENTIONS       Alt andet.
=============  ============================================================

Markører FØR navnet afgøres efter nærhed: den sidste markør inden navnet
vinder. "applies within the Baltic Sea area, excluding the Kattegat" giver
derfor APPLIES for Østersøen og EXEMPTS for Kattegat.

Resultatet er en vurdering med en confidence — aldrig en juridisk
afgørelse. Et menneske bekræfter eller afviser den (se `review_status`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import fold
from app.models.geography import Applicability

__all__ = ["Classification", "classify_match", "sentence_bounds"]

#: Hvor langt der højst ses tilbage og frem efter en sætningsgrænse.
_SENTENCE_RADIUS = 400
#: Markører efter navnet skal stå inden for så mange tegn.
_AFTER_WINDOW = 60

# (markør, klassifikation, confidence). Alle markører er foldede.
_BEFORE_CUES: list[tuple[str, Applicability, float]] = [
    # Undtagelser
    ("gaelder ikke", Applicability.EXEMPTS, 0.75),
    ("finder ikke anvendelse", Applicability.EXEMPTS, 0.75),
    ("shall not apply", Applicability.EXEMPTS, 0.75),
    ("does not apply", Applicability.EXEMPTS, 0.75),
    ("do not apply", Applicability.EXEMPTS, 0.75),
    ("not applicable", Applicability.EXEMPTS, 0.7),
    ("med undtagelse af", Applicability.EXEMPTS, 0.75),
    ("undtagen", Applicability.EXEMPTS, 0.7),
    ("bortset fra", Applicability.EXEMPTS, 0.7),
    ("excluding", Applicability.EXEMPTS, 0.75),
    ("except", Applicability.EXEMPTS, 0.7),
    ("other than", Applicability.EXEMPTS, 0.6),
    ("outside", Applicability.EXEMPTS, 0.6),
    ("uden for", Applicability.EXEMPTS, 0.6),
    ("udenfor", Applicability.EXEMPTS, 0.6),
    ("exempt", Applicability.EXEMPTS, 0.65),
    ("fritaget", Applicability.EXEMPTS, 0.65),
    # Definitioner
    ("definition", Applicability.DEFINES, 0.65),
    ("designated as", Applicability.DEFINES, 0.7),
    ("udpeget som", Applicability.DEFINES, 0.7),
    ("udpeges som", Applicability.DEFINES, 0.7),
    # Anvendelse
    ("gaelder for", Applicability.APPLIES, 0.85),
    ("gaelder i", Applicability.APPLIES, 0.85),
    ("gaelder inden for", Applicability.APPLIES, 0.85),
    ("finder anvendelse", Applicability.APPLIES, 0.85),
    ("applies to", Applicability.APPLIES, 0.85),
    ("apply to", Applicability.APPLIES, 0.85),
    ("applies in", Applicability.APPLIES, 0.85),
    ("apply in", Applicability.APPLIES, 0.85),
    ("applicable to", Applicability.APPLIES, 0.8),
    ("applicable in", Applicability.APPLIES, 0.8),
    ("shall apply", Applicability.APPLIES, 0.85),
    ("within", Applicability.APPLIES, 0.8),
    ("inden for", Applicability.APPLIES, 0.8),
    ("indenfor", Applicability.APPLIES, 0.8),
    ("in the waters of", Applicability.APPLIES, 0.8),
    ("i farvandet", Applicability.APPLIES, 0.75),
    ("operating in", Applicability.APPLIES, 0.8),
    ("operate in", Applicability.APPLIES, 0.8),
    ("navigating in", Applicability.APPLIES, 0.8),
    ("sejlads i", Applicability.APPLIES, 0.8),
    ("sejler i", Applicability.APPLIES, 0.8),
    ("sejle i", Applicability.APPLIES, 0.75),
    ("while in", Applicability.APPLIES, 0.75),
    ("when in", Applicability.APPLIES, 0.75),
    ("applies", Applicability.APPLIES, 0.7),
    ("gaelder", Applicability.APPLIES, 0.7),
]

_AFTER_CUES: list[tuple[str, Applicability, float]] = [
    ("shall not apply", Applicability.EXEMPTS, 0.7),
    ("does not apply", Applicability.EXEMPTS, 0.7),
    ("gaelder ikke", Applicability.EXEMPTS, 0.7),
    ("finder ikke anvendelse", Applicability.EXEMPTS, 0.7),
    ("er undtaget", Applicability.EXEMPTS, 0.7),
    ("undtages", Applicability.EXEMPTS, 0.7),
    ("is excluded", Applicability.EXEMPTS, 0.7),
    ("are excluded", Applicability.EXEMPTS, 0.7),
    ("is exempt", Applicability.EXEMPTS, 0.7),
    ("shall mean", Applicability.DEFINES, 0.85),
    ("means", Applicability.DEFINES, 0.8),
    ("betyder", Applicability.DEFINES, 0.8),
    ("forstaas", Applicability.DEFINES, 0.85),
    ("defineres", Applicability.DEFINES, 0.8),
    ("is defined", Applicability.DEFINES, 0.8),
    ("er defineret", Applicability.DEFINES, 0.8),
    ("comprises", Applicability.DEFINES, 0.7),
    ("omfatter", Applicability.DEFINES, 0.65),
    ("is bounded", Applicability.DEFINES, 0.75),
    ("bounded by", Applicability.DEFINES, 0.75),
    ("afgraenses", Applicability.DEFINES, 0.75),
    ("er afgraenset", Applicability.DEFINES, 0.75),
]

_HISTORICAL_CUES = [
    "ophaevet", "ophaeves", "repealed", "no longer", "ikke laengere",
    "tidligere gaeldende", "formerly", "previously applied",
]

#: Forpligtelser. Sammen med et stedsforholdsord lige før navnet: svag APPLIES.
_OBLIGATION_RE = re.compile(
    r"\b(?:skal|must|shall|maa ikke|may not|forbudt|prohibited|required|paabudt)\b"
)
_LOCATIVE_TAIL_RE = re.compile(
    r"(?:\b(?:i|in|paa|inside|throughout|for|til|to)(?:\s+(?:the|hele|det|den))?)\s*$"
)

_HISTORIC_STATUSES = {"historisk", "ophaevet", "repealed", "historic"}

_BOUNDARY_RE = re.compile(r"\n|[.;](?=\s+[A-ZÆØÅ§(])")


@dataclass(slots=True)
class Classification:
    applicability: Applicability
    confidence: float
    cue: str | None


def sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Den sætning eller linje, positionerne ligger i."""
    lo = max(0, start - _SENTENCE_RADIUS)
    sentence_start = lo
    for m in _BOUNDARY_RE.finditer(text, lo, start):
        sentence_start = m.end()
    hi = min(len(text), end + _SENTENCE_RADIUS)
    m = _BOUNDARY_RE.search(text, end, hi)
    sentence_end = m.start() + (0 if m.group(0) == "\n" else 1) if m else hi
    return sentence_start, sentence_end


def _nearest_before(before: str) -> tuple[str, Applicability, float] | None:
    """Den sidste markør før navnet. Ved samme startposition vinder den længste."""
    best: tuple[int, int, tuple[str, Applicability, float]] | None = None
    for cue in _BEFORE_CUES:
        for m in re.finditer(rf"\b{re.escape(cue[0])}\b", before):
            key = (m.start(), len(cue[0]))
            if best is None or key > best[:2]:
                best = (*key, cue)
    return best[2] if best else None


def _first_after(after: str) -> tuple[str, Applicability, float] | None:
    best: tuple[int, tuple[str, Applicability, float]] | None = None
    for cue in _AFTER_CUES:
        m = re.search(rf"\b{re.escape(cue[0])}\b", after)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), cue)
    return best[1] if best else None


def classify_match(
    text: str,
    start: int,
    end: int,
    *,
    source_field: str = "content",
    document_status: str | None = None,
    mention_only: bool = False,
    weight: float = 1.0,
    quality: float = 1.0,
) -> Classification:
    """Klassificér fundet `text[start:end]` ud fra sætningen omkring det."""
    s_start, s_end = sentence_bounds(text, start, end)
    before = fold(text[s_start:start])
    after = fold(text[end:min(s_end, end + _AFTER_WINDOW)])
    sentence = fold(text[s_start:s_end])

    result = _classify(before, after, sentence, source_field)

    if mention_only and result.applicability != Applicability.MENTIONS:
        result = Classification(Applicability.MENTIONS, min(result.confidence, 0.4), result.cue)
    if fold(document_status or "").strip() in _HISTORIC_STATUSES:
        result = Classification(Applicability.HISTORICAL, result.confidence, "document_status")

    confidence = max(0.0, min(1.0, round(result.confidence * weight * quality, 3)))
    return Classification(result.applicability, confidence, result.cue)


def _classify(before: str, after: str, sentence: str, source_field: str) -> Classification:
    nearest = _nearest_before(before)
    following = _first_after(after)

    # En markør efter navnet ("... Østersøen forstås ...", "... shall not
    # apply") tæller kun, når der ikke allerede står en klar
    # anvendelsesmarkør før navnet: "applies to ships in the North Sea and
    # does not apply to warships" handler om krigsskibe, ikke om Nordsøen.
    if following and not (nearest and nearest[1] == Applicability.APPLIES and nearest[2] >= 0.8):
        return Classification(following[1], following[2], following[0])

    for cue in _HISTORICAL_CUES:
        if re.search(rf"\b{re.escape(cue)}\b", sentence):
            return Classification(Applicability.HISTORICAL, 0.6, cue)

    if nearest:
        return Classification(nearest[1], nearest[2], nearest[0])

    if _LOCATIVE_TAIL_RE.search(before):
        if source_field == "title":
            return Classification(Applicability.APPLIES, 0.75, "title_locative")
        if _OBLIGATION_RE.search(sentence):
            return Classification(Applicability.APPLIES, 0.6, "obligation_locative")

    return Classification(Applicability.MENTIONS, 0.4, None)

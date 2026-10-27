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

Nægtelser
=========
En nægtet anvendelse ("gælder dog ikke", "gælder heller ikke", "finder
dog ikke anvendelse", "er ikke omfattet") afgøres FØR markørerne og kan
aldrig blive til APPLIES — heller ikke med en høj confidence:

* Nægtelsen før navnet styrer resten af sætningen, også en ledsætning:
  "gælder ikke, når skibet sejler i Nordsøen" giver EXEMPTS. Kun et
  modsætningsord ("men", "but") bagefter afslutter den.
* Står der en ny anvendelsesmarkør efter nægtelsen uden modsætningsord,
  er sætningen tvetydig og bliver MENTIONS.
* Står der et løst "ikke"/"not" foran navnet, og ville resultatet ellers
  være APPLIES, bliver det MENTIONS ("gælder for skibe, der ikke sejler
  i Nordsøen"). Forbud ("må ikke", "shall not") er ikke nægtelser.

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
    ("undtaget", Applicability.EXEMPTS, 0.7),
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

#: Biord, der kan stå mellem udsagnsordet og "ikke" ("gælder dog ikke").
_ADVERBS = r"(?:\s+(?:dog|heller|imidlertid|saaledes|derfor|fortsat|altsaa|endvidere|desuden|da|nu|however))*"

#: Nægtet anvendelse (foldet tekst). Afgøres før alle andre markører.
_NEGATED_APPLICATION_RE = re.compile(
    "|".join(
        (
            rf"\bgaelder{_ADVERBS}\s+ikke\b",
            rf"\bfinder{_ADVERBS}\s+ikke\s+anvendelse\b",
            rf"\banvendes{_ADVERBS}\s+ikke\b",
            rf"\bomfattes{_ADVERBS}\s+ikke\b",
            r"\bikke\s+(?:\w+\s+){0,2}?omfattet\b",
            r"\b(?:er|bliver)\s+(?:\w+\s+){0,2}?undtaget\b",
            r"\bundtages\b",
            r"\bundtaget\s+herfra\b",
            r"\b(?:does|do|shall|will|is|are)\s+not\s+(?:apply|be\s+applicable|applicable)\b",
            r"\b(?:is|are|shall\s+be)\s+(?:excluded|exempt(?:ed)?)\b",
        )
    )
)
#: Afslutter en nægtelses rækkevidde: "gælder ikke for X, men gælder i Y".
_CONTRAST_RE = re.compile(r"\b(?:men|but|derimod|however)\b")
#: Afgrænser et led efter navnet: en nægtelse dér handler om noget andet.
_CLAUSE_SEPARATOR_RE = re.compile(r"[,;:]|\b(?:og|and|men|but|samt|eller|or)\b")
#: En ny anvendelse efter nægtelsen gør sætningen tvetydig.
_APPLICATION_PREDICATE_RE = re.compile(
    r"\b(?:gaelder|finder\s+anvendelse|applies|apply|shall\s+apply|applicable|omfattet\s+af)\b"
)
#: Forbud er ikke nægtelser af anvendelsen: "skibe må ikke sejle i Kattegat".
#: "skal ikke" er det ikke — det betyder "behøver ikke" og forbliver en nægtelse.
_PROHIBITION_RE = re.compile(r"\bmaa\s+ikke\b|\b(?:may|must|shall)\s+not\b")
_BARE_NEGATION_RE = re.compile(r"\b(?:ikke|not|never|aldrig|ingen|no)\b")

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


def _cue_name(match: re.Match) -> str:
    return re.sub(r"\s+", " ", match.group(0))[:128]


def _after_negation(after: str) -> Classification | None:
    """En nægtelse lige efter navnet, i samme led: "Kattegat er ikke omfattet"."""
    m = _NEGATED_APPLICATION_RE.search(after)
    if m is None or _CLAUSE_SEPARATOR_RE.search(after[: m.start()]):
        return None
    return Classification(Applicability.EXEMPTS, 0.7, _cue_name(m))


def _historical(sentence: str) -> Classification | None:
    for cue in _HISTORICAL_CUES:
        if re.search(rf"\b{re.escape(cue)}\b", sentence):
            return Classification(Applicability.HISTORICAL, 0.6, cue)
    return None


def _classify(before: str, after: str, sentence: str, source_field: str) -> Classification:
    # Nægtelser afgøres først og kan aldrig blive til APPLIES (se modulets docstring).
    negated = _after_negation(after)
    negations = list(_NEGATED_APPLICATION_RE.finditer(before))
    if negated is None and negations:
        negation = negations[-1]
        tail = before[negation.end():]
        contrast = None
        for contrast in _CONTRAST_RE.finditer(tail):
            pass
        if contrast is not None:
            # "gælder ikke for X, men gælder i Y": kun leddet efter modsætningen tæller.
            before = sentence = tail[contrast.end():]
        elif _APPLICATION_PREDICATE_RE.search(tail):
            negated = Classification(Applicability.MENTIONS, 0.4, "negation_ambiguous")
        else:
            negated = Classification(Applicability.EXEMPTS, 0.75, _cue_name(negation))
    if negated is not None:
        return _historical(sentence) or negated

    result = _affirmative(before, after, sentence, source_field)
    if result.applicability == Applicability.APPLIES and _BARE_NEGATION_RE.search(
        _PROHIBITION_RE.sub(" ", before)
    ):
        return Classification(Applicability.MENTIONS, 0.4, "negation_ambiguous")
    return result


def _affirmative(before: str, after: str, sentence: str, source_field: str) -> Classification:
    """Markørerne, når der ikke står en nægtet anvendelse om navnet."""
    nearest = _nearest_before(before)
    following = _first_after(after)

    # En markør efter navnet ("... Østersøen forstås ...") tæller kun, når
    # der ikke allerede står en klar anvendelsesmarkør før navnet.
    if following and not (nearest and nearest[1] == Applicability.APPLIES and nearest[2] >= 0.8):
        return Classification(following[1], following[2], following[0])

    historical = _historical(sentence)
    if historical:
        return historical

    if nearest:
        return Classification(nearest[1], nearest[2], nearest[0])

    if _LOCATIVE_TAIL_RE.search(before):
        if source_field == "title":
            return Classification(Applicability.APPLIES, 0.75, "title_locative")
        if _OBLIGATION_RE.search(sentence):
            return Classification(Applicability.APPLIES, 0.6, "obligation_locative")

    return Classification(Applicability.MENTIONS, 0.4, None)

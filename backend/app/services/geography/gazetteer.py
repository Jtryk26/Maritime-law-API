"""Gazetteer: kuraterede stednavne og genkendelse af dem i tekst.

Genkendelsen er bevidst regelbaseret — ingen sprogmodel. Hvert alias i
config/geographic_areas.yaml bliver et regulært udtryk, der:

* er uafhængigt af store/små bogstaver og af stavemåden "ø"/"oe"
  (samme foldning som resten af systemet, se `app.core.text`);
* tillader vilkårligt whitespace mellem ordene;
* tillader danske bøjningsendelser ("Østersø" -> "Østersøen", "Østersøens");
* kræver ordgrænse på begge sider, så "Norge" ikke findes i "Norgesbank".

Overlappende fund afgøres til fordel for det længste: "Baltic Sea area"
(MARPOL-særområdet) vinder over "Baltic Sea" (havet).

Derudover en snæver fuzzy-matchning for stavefejl i egennavne
("Skagerack" -> Skagerrak) og et mønster for stednavne, gazetteeren ikke
kender. De sidste bliver aldrig til et område — kun til et punkt i
gennemgangskøen.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings
from app.core.text import _raw_term_pattern, fold

__all__ = [
    "GazetteerEntry",
    "GazetteerMatch",
    "Gazetteer",
    "UnknownPlaceCandidate",
    "find_unknown_places",
    "get_gazetteer",
]

#: Danske bøjningsendelser efter sidste ord i et alias. Længste først.
_SUFFIX = r"(?:ernes|erne|rnes|rne|ens|ets|en|et|ne|ns|ts|s|n|t|r)?"
#: Fuzzy-matchning: mindste lighed og mindste ordlængde.
FUZZY_MIN_RATIO = 0.88
FUZZY_MIN_LENGTH = 6
#: Confidence-faktor for et fuzzy-fund i forhold til et eksakt.
FUZZY_QUALITY = 0.85

_CAPITALIZED_TOKEN_RE = re.compile(r"(?<!\w)[A-ZÆØÅ][a-zæøåäöü]{5,}(?!\w)")


@dataclass(slots=True)
class GazetteerEntry:
    """Ét område som defineret i konfigurationen."""

    slug: str
    name: str
    area_type: str
    aliases: list[str]
    name_da: str | None = None
    parent: str | None = None
    legal_status: str = "curated_unverified"
    legal_basis: str | None = None
    source_url: str | None = None
    source_reference: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    indicative_point: tuple[float, float] | None = None
    mention_only: bool = False
    weight: float = 1.0
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class GazetteerMatch:
    """Et fund af et alias i en tekst. Positionerne er i den givne tekst."""

    slug: str
    start: int
    end: int
    text: str
    alias: str
    #: "gazetteer" eller "gazetteer_fuzzy".
    method: str = "gazetteer"
    #: 1.0 for eksakt, lavere for fuzzy.
    quality: float = 1.0


@dataclass(slots=True)
class UnknownPlaceCandidate:
    """Et ord, der ligner et stednavn, men ikke findes i gazetteeren."""

    key: str
    text: str
    start: int
    end: int


def _alias_pattern(alias: str) -> re.Pattern[str]:
    words = fold(alias).split()
    body = r"\s+".join(_raw_term_pattern(word).pattern for word in words)
    return re.compile(rf"(?<!\w){body}{_SUFFIX}(?!\w)", re.IGNORECASE)


class Gazetteer:
    """De kuraterede områder og deres kompilerede aliasmønstre."""

    def __init__(self, entries: list[GazetteerEntry], fingerprint: str | None = None):
        self.entries = {entry.slug: entry for entry in entries}
        #: Ændres, når et område eller alias ændres. Indgår i analysens
        #: input_hash, så en ændret gazetteer giver en ny analyse.
        self.fingerprint = fingerprint or hashlib.sha256(
            repr(sorted((e.slug, e.area_type, tuple(e.aliases), e.legal_basis, str(e.effective_from),
                         repr(e.metadata)) for e in entries)).encode("utf-8")
        ).hexdigest()
        self._patterns: list[tuple[str, str, re.Pattern[str]]] = [
            (entry.slug, alias, _alias_pattern(alias))
            for entry in entries
            for alias in entry.aliases
        ]
        #: Enkeltords-aliasser til fuzzy-matchning, foldet.
        self._fuzzy: list[tuple[str, str, str]] = [
            (entry.slug, alias, fold(alias))
            for entry in entries
            for alias in entry.aliases
            if " " not in alias.strip() and len(alias) >= FUZZY_MIN_LENGTH
        ]
        self.folded_aliases = {fold(alias) for entry in entries for alias in entry.aliases}

    @classmethod
    def from_yaml(cls, path: Path) -> "Gazetteer":
        raw_text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text) or {}
        entries = []
        for raw in data.get("areas", []):
            point = raw.get("indicative_point")
            entries.append(
                GazetteerEntry(
                    slug=raw["slug"],
                    name=raw["name"],
                    name_da=raw.get("name_da"),
                    area_type=raw["area_type"],
                    aliases=[str(a) for a in raw.get("aliases", [])],
                    parent=raw.get("parent"),
                    legal_status=raw.get("legal_status", "curated_unverified"),
                    legal_basis=raw.get("legal_basis"),
                    source_url=raw.get("source_url"),
                    source_reference=raw.get("source_reference"),
                    effective_from=raw.get("effective_from"),
                    effective_to=raw.get("effective_to"),
                    indicative_point=(float(point[0]), float(point[1])) if point else None,
                    mention_only=bool(raw.get("mention_only", False)),
                    weight=float(raw.get("weight", 1.0)),
                    metadata=dict(raw.get("metadata") or {}),
                )
            )
        return cls(entries, fingerprint=hashlib.sha256(raw_text.encode("utf-8")).hexdigest())

    def find(self, text: str, *, fuzzy: bool = True) -> list[GazetteerMatch]:
        """Alle ikke-overlappende fund i `text`, sorteret efter position."""
        if not text:
            return []
        found: list[GazetteerMatch] = []
        for slug, alias, pattern in self._patterns:
            for m in pattern.finditer(text):
                found.append(GazetteerMatch(slug, m.start(), m.end(), m.group(0), alias))
        chosen = _non_overlapping(found)
        if fuzzy:
            chosen = _non_overlapping(chosen + self._fuzzy_matches(text, chosen))
        return chosen

    def _fuzzy_matches(self, text: str, exact: list[GazetteerMatch]) -> list[GazetteerMatch]:
        """Stavevarianter af enkeltords-egennavne. Kun ord med stort begyndelsesbogstav."""
        taken = [(m.start, m.end) for m in exact]
        results: list[GazetteerMatch] = []
        seen: dict[str, tuple[str, str] | None] = {}
        for m in _CAPITALIZED_TOKEN_RE.finditer(text):
            if any(s <= m.start() < e for s, e in taken):
                continue
            token = fold(m.group(0))
            if token in self.folded_aliases:
                continue
            if token not in seen:
                seen[token] = self._best_fuzzy(token)
            best = seen[token]
            if best:
                slug, alias = best
                results.append(
                    GazetteerMatch(
                        slug, m.start(), m.end(), m.group(0), alias,
                        method="gazetteer_fuzzy", quality=FUZZY_QUALITY,
                    )
                )
        return results

    def _best_fuzzy(self, token: str) -> tuple[str, str] | None:
        best: tuple[float, str, str] | None = None
        for slug, alias, folded in self._fuzzy:
            if folded[0] != token[0] or abs(len(folded) - len(token)) > 3:
                continue
            ratio = SequenceMatcher(None, folded, token).ratio()
            if ratio >= FUZZY_MIN_RATIO and (best is None or ratio > best[0]):
                best = (ratio, slug, alias)
        return (best[1], best[2]) if best else None


def _non_overlapping(matches: list[GazetteerMatch]) -> list[GazetteerMatch]:
    """Længste fund vinder; ved lige længde det tidligste og det eksakte."""
    ordered = sorted(matches, key=lambda m: (-(m.end - m.start), m.start, -m.quality))
    chosen: list[GazetteerMatch] = []
    for match in ordered:
        if all(match.end <= c.start or match.start >= c.end for c in chosen):
            chosen.append(match)
    return sorted(chosen, key=lambda m: m.start)


# -- Ukendte stednavne ------------------------------------------------------

_UNKNOWN_PATTERNS = [
    # "Gulf of Finland", "Bay of Biscay", "Sea of Azov"
    re.compile(r"(?<!\w)(?:Gulf|Bay|Sea|Strait)\s+of\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?(?!\w)"),
    # "Bothnian Sea", "Irish Sea", "Celtic Sea", "Moray Firth"
    re.compile(
        r"(?<!\w)(?:[A-Z][a-z]+\s+){1,2}(?:Sea|Strait|Gulf|Bay|Channel|Bight|Fjord|Firth)(?!\w)"
    ),
    # "Limfjorden", "Aabenraa Fjord", "Flensborg Fjord", "Sejerøbugten"
    re.compile(
        r"(?<!\w)[A-ZÆØÅ][a-zæøå]{2,}(?:fjorden|fjord|bugten|bugt|sundet|bæltet|dybet)(?!\w)"
    ),
    re.compile(r"(?<!\w)[A-ZÆØÅ][a-zæøå]{2,}\s+(?:Fjord|Bugt)(?!\w)"),
]

#: Ord, der opfanges af mønstrene, men ikke er stednavne.
_UNKNOWN_STOPWORDS = {"the", "this", "that", "any", "each", "such", "and", "or", "in", "of"}


def find_unknown_places(
    text: str, gazetteer: "Gazetteer", known: list[GazetteerMatch], *, limit: int = 20
) -> list[UnknownPlaceCandidate]:
    """Ord, der ligner stednavne, og som hverken er et alias eller overlapper et fund."""
    taken = [(m.start, m.end) for m in known]
    out: dict[str, UnknownPlaceCandidate] = {}
    for pattern in _UNKNOWN_PATTERNS:
        for m in pattern.finditer(text):
            words = m.group(0).split()
            while words and words[0].lower() in _UNKNOWN_STOPWORDS:
                words.pop(0)
            if len(words) < 1:
                continue
            value = " ".join(words)
            start = m.end() - len(m.group(0)) + m.group(0).find(words[0])
            end = m.end()
            if any(start < e and end > s for s, e in taken):
                continue
            key = fold(value)
            if key in gazetteer.folded_aliases or key in out:
                continue
            out[key] = UnknownPlaceCandidate(key=key[:255], text=value, start=start, end=end)
            if len(out) >= limit:
                return list(out.values())
    return list(out.values())


@lru_cache(maxsize=4)
def _load(path: str) -> Gazetteer:
    return Gazetteer.from_yaml(Path(path))


def get_gazetteer() -> Gazetteer:
    return _load(str(get_settings().geographic_areas_path))

"""Fælles termmatchning for relevans- og kategoriseringsmotor.

Begge motorer scorer tekst mod konfigurerede termer med feltvægte og
loft på gentagelser. Logikken ligger her, så de to motorer ikke
duplikerer den — og så en ændring i matchsemantikken slår igennem
ét sted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.text import fold

__all__ = ["TermSpec", "FieldHit", "compile_terms", "score_field"]


@dataclass(slots=True)
class TermSpec:
    """En konfigureret term med tilhørende matchregel."""

    term: str
    weight: float
    pattern: re.Pattern[str]
    concept: str | None = None
    match_type: str = "word"


@dataclass(slots=True)
class FieldHit:
    """Et match af én term i ét felt."""

    spec: TermSpec
    occurrences: int
    capped_occurrences: int
    contribution: float


def _build_pattern(term: str, match_type: str, explicit_pattern: str | None) -> re.Pattern[str]:
    """Bygger det regulære udtryk for en term.

    Termen foldes først, så konfigurationen kan skrives med danske tegn
    mens matchningen sker på foldet tekst.
    """
    folded = fold(term)

    if match_type == "regex":
        source = explicit_pattern if explicit_pattern else re.escape(folded)
        # Mønstre i konfigurationen skrives på foldet form.
        return re.compile(source, re.IGNORECASE)

    escaped = re.escape(folded)

    if match_type == "prefix":
        # Ordet som præfiks: "skib" matcher "skibe", "skibsfoerer".
        return re.compile(rf"\b{escaped}\w*", re.IGNORECASE)

    if match_type == "substring":
        # Flerordsudtryk, hvor mellemrum kan variere.
        flexible = r"\s+".join(re.escape(part) for part in folded.split())
        return re.compile(flexible, re.IGNORECASE)

    # Standard: hele ord.
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


def compile_terms(entries: Iterable[dict[str, Any]]) -> list[TermSpec]:
    """Oversætter YAML-poster til færdigkompilerede termer.

    Ugyldige regulære udtryk springes over frem for at vælte opstarten.
    """
    specs: list[TermSpec] = []
    for entry in entries:
        term = str(entry.get("term", "")).strip()
        if not term:
            continue
        match_type = str(entry.get("match", "word")).lower()
        try:
            pattern = _build_pattern(term, match_type, entry.get("pattern"))
        except re.error as exc:  # pragma: no cover - konfigurationsfejl
            raise ValueError(f"Ugyldigt mønster for termen {term!r}: {exc}") from exc

        specs.append(
            TermSpec(
                term=term,
                weight=float(entry.get("weight", 1.0)),
                pattern=pattern,
                concept=entry.get("concept"),
                match_type=match_type,
            )
        )
    return specs


def score_field(
    text: str,
    specs: Iterable[TermSpec],
    *,
    field_weight: float,
    max_occurrences: int,
) -> list[FieldHit]:
    """Scorer én tekstblok mod alle termer.

    Antallet af forekomster pr. term begrænses af `max_occurrences`, så
    gentagelse af det samme ord ikke kan drive scoren mod maksimum.
    Bidraget er:

        min(forekomster, loft) * termvægt * feltvægt
    """
    if not text:
        return []

    folded = fold(text)
    if not folded:
        return []

    hits: list[FieldHit] = []
    for spec in specs:
        occurrences = len(spec.pattern.findall(folded))
        if occurrences == 0:
            continue
        capped = min(occurrences, max_occurrences)
        hits.append(
            FieldHit(
                spec=spec,
                occurrences=occurrences,
                capped_occurrences=capped,
                contribution=capped * spec.weight * field_weight,
            )
        )
    return hits

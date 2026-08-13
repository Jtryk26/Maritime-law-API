"""Kontrakt for maritim relevansvurdering.

Importeren kender kun denne grænseflade. Regelmotoren i Version 1 kan
derfor senere suppleres eller erstattes af en AI-baseret motor uden
ændringer i importer, persistering eller API.

Resultatet er bevidst gennemsigtigt. Systemet arbejder med lovgivning,
hvor en uforklaret klassifikation er ubrugelig: en bruger skal kunne se
hvilke termer der talte, hvor de stod, hvad de bidrog med, hvilke
negative signaler der trak fra, og hvordan den samlede score fremkom.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.services.retsinformation.base import NormalizedDocument

__all__ = ["RelevanceResult", "TermMatch", "RelevanceEngine"]


@dataclass(slots=True)
class TermMatch:
    """En enkelt term der bidrog til scoren — positivt eller negativt."""

    term: str
    field: str
    occurrences: int
    #: Forekomster efter loft. Viser hvornår anti-spam-loftet slog til.
    counted_occurrences: int
    weight: float
    field_weight: float
    contribution: float
    concept: str | None = None

    def to_json(self) -> dict:
        return {
            "term": self.term,
            "field": self.field,
            "occurrences": self.occurrences,
            "counted_occurrences": self.counted_occurrences,
            "capped": self.occurrences > self.counted_occurrences,
            "term_weight": self.weight,
            "field_weight": self.field_weight,
            "contribution": round(self.contribution, 2),
            "concept": self.concept,
        }


@dataclass(slots=True)
class RelevanceResult:
    """Resultatet af en relevansvurdering.

    `score` er normaliseret til 0-100. De øvrige felter gør vurderingen
    reproducerbar: regnestykket kan genskabes fra `matches`,
    `negative_matches`, `concept_bonus` og `saturation`.
    """

    is_maritime: bool
    score: int
    matched_terms: list[str] = field(default_factory=list)
    reason: str = ""
    #: "maritime" | "possible" | "not_maritime"
    classification: str = "not_maritime"
    matches: list[TermMatch] = field(default_factory=list)
    negative_matches: list[TermMatch] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)

    # --- Regnestykkets bestanddele -----------------------------------------
    positive_raw: float = 0.0
    negative_raw: float = 0.0
    concept_bonus: float = 0.0
    raw_score: float = 0.0
    saturation: float = 0.0
    #: Score pr. felt, saa det er synligt om signalet kom fra titel eller tekst.
    field_contributions: dict[str, float] = field(default_factory=dict)
    #: Sat naar titelautoritetsreglen loeftede scoren til gulvet.
    title_floor_applied: bool = False
    title_floor_terms: list[str] = field(default_factory=list)
    thresholds: dict[str, int] = field(default_factory=dict)

    engine: str = "unknown"

    def to_json(self) -> dict:
        """Serialiserbar form. Gemmes paa dokumentet og vises i brugerfladen."""
        return {
            "engine": self.engine,
            "is_maritime": self.is_maritime,
            "score": self.score,
            "classification": self.classification,
            "reason": self.reason,
            "matched_terms": self.matched_terms,
            "concepts": self.concepts,
            "calculation": {
                "positive_raw": round(self.positive_raw, 2),
                "concept_bonus": round(self.concept_bonus, 2),
                "negative_raw": round(self.negative_raw, 2),
                "raw_score": round(self.raw_score, 2),
                "saturation": self.saturation,
                "normalized_score": self.score,
                "title_floor_applied": self.title_floor_applied,
                "title_floor_terms": self.title_floor_terms,
                "field_contributions": {
                    k: round(v, 2) for k, v in self.field_contributions.items()
                },
                "thresholds": self.thresholds,
            },
            "matches": [m.to_json() for m in self.matches],
            "negative_matches": [m.to_json() for m in self.negative_matches],
        }


@runtime_checkable
class RelevanceEngine(Protocol):
    """Enhver relevansmotor opfylder denne kontrakt.

    Version 1 implementerer :class:`KeywordRelevanceEngine`. Fremtidige
    motorer - AIRelevanceEngine, EmbeddingRelevanceEngine,
    HybridRelevanceEngine - implementerer den samme metode.
    """

    name: str

    def classify(self, document: NormalizedDocument) -> RelevanceResult:
        """Vurderer om dokumentet er maritimt relevant."""
        ...

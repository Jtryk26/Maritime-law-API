"""Regelbaseret maritim relevansmotor.

Scoringsmodel
=============
Dokumentet deles i fire felter med hver sin vægt::

    titel      x 3.0    (stærkeste signal)
    myndighed  x 2.5
    metadata   x 1.5
    brødtekst  x 1.0

For hver term i konfigurationen tælles forekomster pr. felt, men højst
`max_occurrences_per_term_per_field` gange. Det forhindrer at et
dokument der gentager "skib" 500 gange automatisk rammer maksimum::

    bidrag = min(forekomster, loft) x termvægt x feltvægt

Termer hører til begreber (fartøj, besætning, miljø ...). Et dokument
der dækker flere uafhængige begreber får bonus — bredde er et bedre
maritimt signal end dybde i ét enkelt ord::

    bonus = (antal begreber - 1) x bonus_pr_begreb     (med loft)

Negative termer (luftfart, jernbane, folkeskole) trækkes fra og dæmper
falske positiver.

Den rå score normaliseres mættende til 0-100::

    score = 100 x rå / (rå + saturation)

Funktionen er monotont voksende og når aldrig helt 100, hvilket giver
en meningsfuld rangordning i hele skalaen.

Titelautoritet
==============
Står en utvetydig maritim term i titlen, er dokumentet maritimt uanset
tekstlængde. Titlen er lovgivers egen emneangivelse. Reglen sætter et
gulv for scoren og anvendes ikke, hvis titlen samtidig indeholder en
negativ term.

Gennemsigtighed
===============
Alle mellemregninger returneres i :class:`RelevanceResult`, så en bruger
kan efterprøve hvorfor et dokument blev klassificeret som maritimt.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.text import fold
from app.services.matching import FieldHit, TermSpec, compile_terms, score_field
from app.services.retsinformation.base import NormalizedDocument

from .base import RelevanceResult, TermMatch

logger = get_logger(__name__)

__all__ = ["KeywordRelevanceEngine", "load_keyword_config", "get_relevance_engine"]

DEFAULT_FIELD_WEIGHTS = {"title": 3.0, "authority": 2.5, "metadata": 1.5, "content": 1.0}

FIELD_LABELS = {
    "title": "titel",
    "authority": "myndighed",
    "metadata": "metadata",
    "content": "dokumenttekst",
}


def load_keyword_config(path: Path) -> dict[str, Any]:
    """Indlæser og validerer nøgleordskonfigurationen."""
    if not path.exists():
        raise FileNotFoundError(f"Konfigurationsfil mangler: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} skal indeholde et YAML-objekt")
    if not data.get("terms"):
        raise ValueError(f"{path} indeholder ingen termer under 'terms'")
    return data


class KeywordRelevanceEngine:
    """Konfigurationsdrevet relevansmotor.

    Opfylder :class:`~app.services.relevance.base.RelevanceEngine`.
    """

    name = "keyword"

    def __init__(self, config: dict[str, Any] | None = None, *, config_path: Path | None = None):
        if config is None:
            path = config_path or get_settings().maritime_keywords_path
            config = load_keyword_config(path)

        scoring = config.get("scoring", {}) or {}
        thresholds = config.get("thresholds", {}) or {}

        self.field_weights: dict[str, float] = {
            **DEFAULT_FIELD_WEIGHTS,
            **{k: float(v) for k, v in (scoring.get("field_weights") or {}).items()},
        }
        self.max_occurrences = int(scoring.get("max_occurrences_per_term_per_field", 3))
        self.bonus_per_extra_concept = float(scoring.get("bonus_per_extra_concept", 4.0))
        self.max_concept_bonus = float(scoring.get("max_concept_bonus", 20.0))
        self.saturation = float(scoring.get("saturation", 45.0))
        self.decisive_title_min_weight = float(scoring.get("decisive_title_min_weight", 8.0))
        self.decisive_title_floor_score = int(scoring.get("decisive_title_floor_score", 70))

        self.maritime_threshold = int(thresholds.get("maritime", 60))
        self.possible_threshold = int(thresholds.get("possible", 30))

        self.terms: list[TermSpec] = compile_terms(config.get("terms", []))
        self.negative_terms: list[TermSpec] = compile_terms(config.get("negative_terms", []) or [])

        logger.info(
            "relevance.engine.loaded",
            extra={
                "engine": self.name,
                "terms": len(self.terms),
                "negative_terms": len(self.negative_terms),
                "maritime_threshold": self.maritime_threshold,
            },
        )

    # -- Offentlig API ------------------------------------------------------

    def classify(self, document: NormalizedDocument) -> RelevanceResult:
        """Vurderer dokumentets maritime relevans."""
        fields = {
            "title": document.title or "",
            "authority": document.authority or "",
            "metadata": document.metadata_text(),
            "content": document.content or "",
        }

        matches: list[TermMatch] = []
        negative_matches: list[TermMatch] = []
        field_contributions: dict[str, float] = {}
        positive_raw = 0.0
        negative_raw = 0.0

        for field_name, text in fields.items():
            weight = self.field_weights.get(field_name, 1.0)

            for hit in score_field(
                text, self.terms, field_weight=weight, max_occurrences=self.max_occurrences
            ):
                matches.append(self._to_match(field_name, weight, hit))
                positive_raw += hit.contribution
                field_contributions[field_name] = (
                    field_contributions.get(field_name, 0.0) + hit.contribution
                )

            for hit in score_field(
                text,
                self.negative_terms,
                field_weight=weight,
                max_occurrences=self.max_occurrences,
            ):
                negative_matches.append(self._to_match(field_name, weight, hit, negative=True))
                negative_raw += hit.contribution

        # Bonus for bredde: flere uafhængige maritime begreber.
        concepts = sorted({m.concept for m in matches if m.concept})
        concept_bonus = 0.0
        if len(concepts) > 1:
            concept_bonus = min(
                (len(concepts) - 1) * self.bonus_per_extra_concept,
                self.max_concept_bonus,
            )

        raw = max(0.0, positive_raw + concept_bonus - negative_raw)
        score = self._normalize(raw)

        # Titelautoritet.
        decisive_terms = sorted(
            {
                m.term
                for m in matches
                if m.field == "title" and m.weight >= self.decisive_title_min_weight
            }
        )
        title_has_negative = any(m.field == "title" for m in negative_matches)
        title_floor_applied = False
        if decisive_terms and not title_has_negative and score < self.decisive_title_floor_score:
            score = self.decisive_title_floor_score
            title_floor_applied = True

        classification = self._classify_score(score)
        matches.sort(key=lambda m: m.contribution, reverse=True)
        negative_matches.sort(key=lambda m: m.contribution, reverse=True)

        matched_terms: list[str] = []
        for match in matches:
            if match.term not in matched_terms:
                matched_terms.append(match.term)

        return RelevanceResult(
            is_maritime=classification == "maritime",
            score=score,
            matched_terms=matched_terms,
            reason=self._build_reason(
                matches,
                negative_matches,
                concepts,
                concept_bonus,
                classification,
                decisive_terms if title_floor_applied else [],
            ),
            classification=classification,
            matches=matches,
            negative_matches=negative_matches,
            concepts=concepts,
            positive_raw=positive_raw,
            negative_raw=negative_raw,
            concept_bonus=concept_bonus,
            raw_score=raw,
            saturation=self.saturation,
            field_contributions=field_contributions,
            title_floor_applied=title_floor_applied,
            title_floor_terms=decisive_terms if title_floor_applied else [],
            thresholds={
                "maritime": self.maritime_threshold,
                "possible": self.possible_threshold,
            },
            engine=self.name,
        )

    # -- Intern -------------------------------------------------------------

    @staticmethod
    def _to_match(
        field_name: str, field_weight: float, hit: FieldHit, *, negative: bool = False
    ) -> TermMatch:
        return TermMatch(
            term=hit.spec.term,
            field=field_name,
            occurrences=hit.occurrences,
            counted_occurrences=hit.capped_occurrences,
            weight=hit.spec.weight,
            field_weight=field_weight,
            contribution=hit.contribution,
            concept=hit.spec.concept,
        )

    def _normalize(self, raw: float) -> int:
        """Mættende normalisering til 0-100."""
        if raw <= 0:
            return 0
        return int(round(100.0 * raw / (raw + self.saturation)))

    def _classify_score(self, score: int) -> str:
        if score >= self.maritime_threshold:
            return "maritime"
        if score >= self.possible_threshold:
            return "possible"
        return "not_maritime"

    def _build_reason(
        self,
        matches: list[TermMatch],
        negative_matches: list[TermMatch],
        concepts: list[str],
        concept_bonus: float,
        classification: str,
        title_floor_terms: list[str],
    ) -> str:
        """Menneskelæsbar begrundelse til brugerfladen."""
        negative_terms = sorted({m.term for m in negative_matches})

        if not matches:
            if negative_terms:
                return (
                    "Ingen maritime termer fundet. Dokumentet indeholder termer fra "
                    f"et andet fagområde ({', '.join(negative_terms[:3])})."
                )
            return "Ingen maritime termer fundet i titel, myndighed, metadata eller tekst."

        by_field: dict[str, float] = {}
        for match in matches:
            by_field[match.field] = by_field.get(match.field, 0.0) + match.contribution
        strongest = sorted(by_field, key=lambda f: by_field[f], reverse=True)

        fields_text = ", ".join(FIELD_LABELS.get(f, f) for f in strongest[:3])
        top_terms = ", ".join(dict.fromkeys(m.term for m in matches[:4]))

        prefix = {
            "maritime": "Maritim terminologi",
            "possible": "Svag eller delvis maritim terminologi",
            "not_maritime": "Enkelte maritime termer, men for svagt signal",
        }[classification]

        parts = [f"{prefix} i {fields_text}. Stærkeste termer: {top_terms}."]

        if title_floor_terms:
            parts.append(
                "Titlen indeholder utvetydig maritim terminologi "
                f"({', '.join(title_floor_terms[:3])}), hvilket alene er afgørende "
                "for klassifikationen."
            )
        if concept_bonus > 0:
            parts.append(
                f"Dækker {len(concepts)} uafhængige maritime begreber "
                f"({', '.join(concepts[:5])}), hvilket giver breddebonus."
            )
        if negative_terms:
            parts.append(
                "Scoren er dæmpet af termer fra et andet fagområde: "
                f"{', '.join(negative_terms[:3])}."
            )
        return " ".join(parts)


@lru_cache(maxsize=1)
def get_relevance_engine() -> KeywordRelevanceEngine:
    """Delt motorinstans. Konfiguration læses og kompileres én gang."""
    return KeywordRelevanceEngine()

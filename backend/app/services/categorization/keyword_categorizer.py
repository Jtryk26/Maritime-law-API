"""Regelbaseret maritim kategorisering.

Hver kategori i config/categories.yaml har sit eget termsæt. Et dokument
scores mod alle kategorier med samme feltvægtede, loftbegrænsede model
som relevansmotoren.

Kategoriens rå score omregnes til en confidence i 0.0–1.0::

    confidence = rå / (rå + saturation)

Kategorier over `min_confidence` tildeles, højst
`max_categories_per_document` stykker. Nåede ingen kategori tærsklen,
tildeles `fallback_category`, så et maritimt dokument aldrig ender
ukategoriseret.

KONTRAKT
========
Motoren forudsætter at dokumentet allerede er vurderet maritimt
relevant. Taksonomien beskriver emner *inden for* søfartsområdet og
skelner ikke maritimt fra ikke-maritimt — det er relevansmotorens
opgave. Importeren kalder derfor kun denne motor for dokumenter, der
har bestået relevanstærsklen. Kaldes den på et vilkårligt dokument, vil
generiske forvaltningsudtryk kunne udløse en kategori.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.matching import TermSpec, compile_terms, score_field
from app.services.retsinformation.base import NormalizedDocument

from .base import CategoryAssignment, CategorizationResult, CategoryDefinition

logger = get_logger(__name__)

__all__ = [
    "KeywordCategorizationEngine",
    "load_category_config",
    "get_categorization_engine",
]

DEFAULT_FIELD_WEIGHTS = {"title": 3.0, "authority": 1.2, "metadata": 1.5, "content": 1.0}


def load_category_config(path: Path) -> dict[str, Any]:
    """Indlæser og validerer kategorikonfigurationen."""
    if not path.exists():
        raise FileNotFoundError(f"Konfigurationsfil mangler: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} skal indeholde et YAML-objekt")

    categories = data.get("categories")
    if not categories:
        raise ValueError(f"{path} indeholder ingen kategorier")

    seen: set[str] = set()
    for entry in categories:
        slug = entry.get("slug")
        if not slug:
            raise ValueError(f"Kategori uden 'slug' i {path}: {entry.get('name')!r}")
        if slug in seen:
            raise ValueError(f"Duplikeret kategori-slug i {path}: {slug!r}")
        seen.add(slug)

    fallback = data.get("fallback_category")
    if fallback and fallback not in seen:
        raise ValueError(f"fallback_category {fallback!r} findes ikke blandt kategorierne i {path}")

    return data


class _CompiledCategory:
    """En kategori med færdigkompilerede termer."""

    __slots__ = ("definition", "terms")

    def __init__(self, definition: CategoryDefinition, terms: list[TermSpec]) -> None:
        self.definition = definition
        self.terms = terms


class KeywordCategorizationEngine:
    """Konfigurationsdrevet kategoriseringsmotor.

    Opfylder :class:`~app.services.categorization.base.CategorizationEngine`.
    """

    name = "keyword"

    def __init__(self, config: dict[str, Any] | None = None, *, config_path: Path | None = None):
        if config is None:
            path = config_path or get_settings().categories_path
            config = load_category_config(path)

        scoring = config.get("scoring", {}) or {}
        self.field_weights: dict[str, float] = {
            **DEFAULT_FIELD_WEIGHTS,
            **{k: float(v) for k, v in (scoring.get("field_weights") or {}).items()},
        }
        self.max_occurrences = int(scoring.get("max_occurrences_per_term_per_field", 3))
        self.saturation = float(scoring.get("saturation", 12.0))
        self.min_confidence = float(scoring.get("min_confidence", 0.35))
        self.max_categories = int(scoring.get("max_categories_per_document", 6))
        self.fallback_slug: str | None = config.get("fallback_category")

        self._categories: list[_CompiledCategory] = []
        for index, entry in enumerate(config.get("categories", [])):
            definition = CategoryDefinition(
                slug=str(entry["slug"]),
                name=str(entry.get("name", entry["slug"])),
                description=entry.get("description"),
                sort_order=index,
            )
            self._categories.append(
                _CompiledCategory(definition, compile_terms(entry.get("terms") or []))
            )

        self._by_slug = {c.definition.slug: c for c in self._categories}

        logger.info(
            "categorization.engine.loaded",
            extra={
                "engine": self.name,
                "categories": len(self._categories),
                "min_confidence": self.min_confidence,
            },
        )

    # -- Offentlig API ------------------------------------------------------

    def definitions(self) -> list[CategoryDefinition]:
        """Alle kategorier i konfigurationsrækkefølge."""
        return [c.definition for c in self._categories]

    def categorize(self, document: NormalizedDocument) -> CategorizationResult:
        """Tildeler kategorier til dokumentet."""
        fields = {
            "title": document.title or "",
            "authority": document.authority or "",
            "metadata": document.metadata_text(),
            "content": document.content or "",
        }

        candidates: list[CategoryAssignment] = []

        for category in self._categories:
            if not category.terms:
                # Fallback-kategorien har ingen egne termer.
                continue

            raw = 0.0
            matched: list[str] = []

            for field_name, text in fields.items():
                weight = self.field_weights.get(field_name, 1.0)
                for hit in score_field(
                    text,
                    category.terms,
                    field_weight=weight,
                    max_occurrences=self.max_occurrences,
                ):
                    raw += hit.contribution
                    if hit.spec.term not in matched:
                        matched.append(hit.spec.term)

            if raw <= 0:
                continue

            confidence = raw / (raw + self.saturation)
            if confidence >= self.min_confidence:
                candidates.append(
                    CategoryAssignment(
                        slug=category.definition.slug,
                        name=category.definition.name,
                        confidence=round(confidence, 4),
                        matched_terms=matched[:12],
                        raw_score=round(raw, 2),
                    )
                )

        candidates.sort(key=lambda a: a.raw_score, reverse=True)
        assignments = candidates[: self.max_categories]

        # Et maritimt dokument skal aldrig ende ukategoriseret.
        if not assignments and self.fallback_slug:
            fallback = self._by_slug.get(self.fallback_slug)
            if fallback is not None:
                assignments = [
                    CategoryAssignment(
                        slug=fallback.definition.slug,
                        name=fallback.definition.name,
                        confidence=self.min_confidence,
                        matched_terms=[],
                        raw_score=0.0,
                        is_fallback=True,
                    )
                ]

        return CategorizationResult(assignments=assignments, engine=self.name)


@lru_cache(maxsize=1)
def get_categorization_engine() -> KeywordCategorizationEngine:
    """Delt motorinstans."""
    return KeywordCategorizationEngine()

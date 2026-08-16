"""Indlæsning af `config/ranking.yaml`.

Samme mønster som relevansmotoren og kategoriseringen: konfigurationen
læses én gang, foldes til matchvenlig form og caches. Ingen vægt, ingen
nicheterm og intet titelmønster står i Python-koden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.text import fold

logger = get_logger(__name__)

__all__ = [
    "NicheGroup",
    "RankingConfig",
    "load_ranking_config",
    "get_ranking_config",
    "reset_ranking_config",
]

#: Termer på højst så mange tegn kræver ordstart. Se `NicheGroup.matches`.
_SHORT_TERM_CHARS = 5


@dataclass(frozen=True, slots=True)
class NicheGroup:
    """En gruppe dokumenter med smal anvendelse — fx fiskeskibe eller Grønland."""

    slug: str
    label: str
    strength: float
    #: Foldede termer. Matchning er substring på foldet tekst, fordi
    #: nichemarkører optræder i sammensætninger ("grønlandske farvande",
    #: "fiskeskibsfører") hvor ordgrænser ville koste flere match end de
    #: sparer falske positiver.
    terms: tuple[str, ...]

    def matches(self, folded_text: str) -> list[str]:
        """Termerne der findes i teksten.

        Korte termer kræver en ordstart foran sig. Uden det ville "lods"
        matche inde i vilkårlige ord; med en fuld ordgrænse i begge ender
        ville det til gengæld ikke matche "lodser" eller "lodsning", som
        er præcis de former, der optræder i titler.
        """
        hits: list[str] = []
        for term in self.terms:
            if not term:
                continue
            if len(term) <= _SHORT_TERM_CHARS:
                if re.search(rf"(?<![a-z0-9æøå]){re.escape(term)}", folded_text):
                    hits.append(term)
            elif term in folded_text:
                hits.append(term)
        return hits


@dataclass(slots=True)
class RankingConfig:
    """Hele rangeringskonfigurationen i opslagsklar form."""

    weights: dict[str, float]
    rank_decay_k: float
    status_scores: dict[str, float]

    type_scores: dict[str, float]
    authority_scores: dict[str, float]
    type_weight: float
    authority_weight: float
    law_class_adjustment: dict[str, float]

    support_types: frozenset[str]
    support_patterns: tuple[str, ...]
    support_statuses: frozenset[str]
    core_types: frozenset[str]
    core_patterns: tuple[str, ...]
    core_min_maritime_score: int
    core_authorities: frozenset[str]
    core_source_ids: frozenset[str]

    niche_groups: tuple[NicheGroup, ...]
    broad_terms: tuple[str, ...]

    domain_rules: dict[str, Any]
    intent: dict[str, Any]

    raw: dict[str, Any] = field(default_factory=dict)

    # -- Opslag -------------------------------------------------------------

    def status_score(self, status: str | None) -> float:
        key = fold(status or "")
        return float(self.status_scores.get(key, self.status_scores.get("default", 0.5)))

    def type_score(self, document_type: str | None) -> float:
        key = fold(document_type or "")
        return float(self.type_scores.get(key, self.type_scores.get("default", 0.5)))

    def authority_score(self, authority: str | None) -> float:
        key = fold(authority or "")
        return float(self.authority_scores.get(key, self.authority_scores.get("default", 0.45)))

    def rules_for(self, intent_kind: str) -> dict[str, float]:
        return dict(self.domain_rules.get(intent_kind) or self.domain_rules.get("semi") or {})


def _folded_map(raw: dict | None, *, default_key: str = "default") -> dict[str, float]:
    """Foldede nøgler, så "Søfartsstyrelsen" og "soefartsstyrelsen" er ét opslag."""
    result: dict[str, float] = {}
    for key, value in (raw or {}).items():
        try:
            result[fold(str(key))] = float(value)
        except (TypeError, ValueError):
            logger.warning("ranking.config.bad_value", extra={"key": str(key)})
    result.setdefault(default_key, result.get(default_key, 0.5))
    return result


def _folded_tuple(values: Any) -> tuple[str, ...]:
    return tuple(fold(str(v)) for v in (values or []) if str(v).strip())


def _folded_set(values: Any) -> frozenset[str]:
    return frozenset(_folded_tuple(values))


def load_ranking_config(path: Path) -> RankingConfig:
    """Læser og validerer konfigurationsfilen."""
    if not path.exists():
        raise FileNotFoundError(f"Konfigurationsfil mangler: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} skal indeholde et YAML-objekt")

    weights_raw = data.get("weights") or {}
    weights = {
        "lexical": float(weights_raw.get("lexical", 0.40)),
        "semantic": float(weights_raw.get("semantic", 0.25)),
        "authority": float(weights_raw.get("authority", 0.15)),
        "scope": float(weights_raw.get("scope", 0.10)),
        "maritime_relevance": float(weights_raw.get("maritime_relevance", 0.05)),
        "status": float(weights_raw.get("status", 0.05)),
    }
    total = sum(weights.values())
    if total <= 0:
        raise ValueError(f"{path}: vægtene summer til {total}")
    if abs(total - 1.0) > 0.001:
        # Ikke en fejl — men en vægtsum på 1,0 gør scoren læsbar som en
        # andel, og en utilsigtet skævhed skal kunne ses i loggen.
        logger.warning("ranking.config.weights_not_normalised", extra={"sum": round(total, 4)})

    authority_raw = data.get("authority") or {}
    law_class_raw = data.get("law_class") or {}
    support_raw = law_class_raw.get("support") or {}
    core_raw = law_class_raw.get("core") or {}

    groups: list[NicheGroup] = []
    for item in law_class_raw.get("niche_groups") or []:
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        groups.append(
            NicheGroup(
                slug=str(item["slug"]),
                label=str(item.get("label") or item["slug"]),
                strength=float(item.get("strength", 1.0)),
                terms=_folded_tuple(item.get("terms")),
            )
        )

    return RankingConfig(
        weights=weights,
        rank_decay_k=float(data.get("rank_decay_k", 12)),
        status_scores=_folded_map(data.get("status_scores")),
        type_scores=_folded_map(authority_raw.get("document_types")),
        authority_scores=_folded_map(authority_raw.get("authorities")),
        type_weight=float(authority_raw.get("type_weight", 0.6)),
        authority_weight=float(authority_raw.get("authority_weight", 0.4)),
        law_class_adjustment={
            str(k): float(v)
            for k, v in (authority_raw.get("law_class_adjustment") or {}).items()
        },
        support_types=_folded_set(support_raw.get("document_types")),
        support_patterns=_folded_tuple(support_raw.get("title_patterns")),
        support_statuses=_folded_set(support_raw.get("statuses")),
        core_types=_folded_set(core_raw.get("document_types")),
        core_patterns=_folded_tuple(core_raw.get("title_patterns")),
        core_min_maritime_score=int(core_raw.get("min_maritime_score", 60)),
        core_authorities=_folded_set(core_raw.get("authorities")),
        core_source_ids=frozenset(str(s).strip() for s in (core_raw.get("source_ids") or [])),
        niche_groups=tuple(groups),
        broad_terms=_folded_tuple(law_class_raw.get("broad_terms")),
        domain_rules=data.get("domain_rules") or {},
        intent=data.get("query_intent") or {},
        raw=data,
    )


@lru_cache(maxsize=1)
def get_ranking_config() -> RankingConfig:
    """Cachet konfiguration fra `config/ranking.yaml`."""
    settings = get_settings()
    path = settings.config_dir / "ranking.yaml"
    config = load_ranking_config(path)
    logger.info(
        "ranking.config.loaded",
        extra={
            "niche_groups": len(config.niche_groups),
            "core_patterns": len(config.core_patterns),
        },
    )
    return config


def reset_ranking_config() -> None:
    """Rydder cachen. Bruges i test og efter konfigurationsændringer."""
    get_ranking_config.cache_clear()

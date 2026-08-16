"""Domænejusteret rangering.

Tre dele, som deler én konfigurationsfil (``config/ranking.yaml``):

``classification``
    Er dokumentet en kernelov, en speciallov eller et støttedokument? Og
    hvor bredt gælder det (``scope_score``), og hvor tungt vejer det som
    retskilde (``authority_score``)?

``intent``
    Er brugerens søgning bred, semispecifik eller niche?

``scorer``
    Den vægtede formel og de domæneregler, der bringer de to sammen.

Adskillelsen er den samme som mellem relevansmotor og importer: hver del
kan afprøves for sig, og en senere AI-baseret klassifikation kan træde i
stedet for ``LawClassifier`` uden at røre hverken søgning eller API.
"""

from .classification import LawClass, LawClassResult, LawClassifier, classify_law_class
from .config import (
    NicheGroup,
    RankingConfig,
    get_ranking_config,
    load_ranking_config,
    reset_ranking_config,
)
from .intent import INTENT_KINDS, QueryIntent, classify_query_intent, refine_intent
from .scorer import DomainRanker, RankingAdjustment, RankingBreakdown, RankingSignals

__all__ = [
    "LawClass",
    "LawClassResult",
    "LawClassifier",
    "classify_law_class",
    "NicheGroup",
    "RankingConfig",
    "get_ranking_config",
    "load_ranking_config",
    "reset_ranking_config",
    "QueryIntent",
    "classify_query_intent",
    "refine_intent",
    "INTENT_KINDS",
    "DomainRanker",
    "RankingSignals",
    "RankingBreakdown",
    "RankingAdjustment",
]

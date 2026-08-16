"""Den samlede rangeringsmodel.

Formlen
=======
.. code-block:: text

    base =
      0.40 * lexical_score +
      0.25 * semantic_score +
      0.15 * authority_score +
      0.10 * scope_score +
      0.05 * maritime_relevance_score +
      0.05 * status_score

    final = base * produktet af domænereglerne

Alle vægte står i ``config/ranking.yaml`` og kan ændres uden kodeændring.

Hvorfor placering og ikke rå score
==================================
``ts_rank_cd`` er et ubegrænset tal, der afhænger af dokumentets længde og
af hvor mange gange termen står der. Cosinus-lighed ligger mellem 0 og 1
og er sammenpresset i den øvre ende. De to kan ikke lægges sammen som
tal — det er den samme indsigt, der oprindeligt førte til Reciprocal Rank
Fusion i hybridsøgningen.

Derfor omregnes hver delsøgnings resultat til en **placeringsbaseret**
score::

    score = k / (k + placering - 1)

Nr. 1 får 1,0, nr. 2 får k/(k+1), og faldet er blødt. Det eneste, der
bruges fra delsøgningerne, er altså rækkefølgen — det eneste de to er
enige om at måle. Til gengæld er skalaen nu 0–1 for begge, og den vægtede
sum i brief'et kan udregnes meningsfuldt.

Hvorfor multiplikatorer og ikke flere led
=========================================
Domænereglerne kunne have været led i summen. De er multiplikatorer,
fordi de skal kunne **forklares enkeltvis**: brugerfladen viser
"nedjusteret 30 % — speciallov ved bred søgning", og det tal svarer til
noget. Et ekstra led i en vægtet sum kan ikke oversættes til en sætning.

Ingen multiplikator må kunne nulstille et resultat (``min_multiplier``).
Et dokument der matcher søgningen, skal kunne findes; det skal blot stå
længere nede.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.text import fold

from .classification import LawClass
from .config import RankingConfig, get_ranking_config
from .intent import QueryIntent

__all__ = [
    "RankingSignals",
    "RankingAdjustment",
    "RankingBreakdown",
    "DomainRanker",
]


@dataclass(slots=True)
class RankingSignals:
    """Alt rangeringen har brug for om ét kandidatdokument."""

    document_id: int
    #: 1-baseret placering i den leksikalske delsøgning. None = ikke fundet.
    lexical_position: int | None = None
    #: 1-baseret placering i den semantiske delsøgning. None = ikke fundet.
    semantic_position: int | None = None
    law_class: str = LawClass.CORE
    scope_score: float = 0.55
    authority_score: float = 0.5
    maritime_score: int = 0
    status: str | None = None
    niche_groups: list[str] = field(default_factory=list)

    @property
    def match_source(self) -> str:
        if self.lexical_position is not None and self.semantic_position is not None:
            return "both"
        if self.semantic_position is not None:
            return "semantic"
        return "lexical"


@dataclass(slots=True)
class RankingAdjustment:
    """Én domæneregel der blev anvendt, med sin begrundelse."""

    name: str
    factor: float
    reason: str

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "factor": round(self.factor, 3),
            "reason": self.reason,
            #: Procentvis ændring, klar til visning: -30 betyder "30 % ned".
            "percent": round((self.factor - 1.0) * 100),
        }


@dataclass(slots=True)
class RankingBreakdown:
    """Regnestykket bag ét resultats placering."""

    document_id: int
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    authority_score: float = 0.0
    scope_score: float = 0.0
    maritime_score: float = 0.0
    status_score: float = 0.0
    base_score: float = 0.0
    multiplier: float = 1.0
    final_score: float = 0.0
    adjustments: list[RankingAdjustment] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "lexical_score": round(self.lexical_score, 4),
            "semantic_score": round(self.semantic_score, 4),
            "authority_score": round(self.authority_score, 4),
            "scope_score": round(self.scope_score, 4),
            "maritime_score": round(self.maritime_score, 4),
            "status_score": round(self.status_score, 4),
            "base_score": round(self.base_score, 4),
            "multiplier": round(self.multiplier, 4),
            "final_score": round(self.final_score, 6),
            "adjustments": [a.to_json() for a in self.adjustments],
        }


class DomainRanker:
    """Beregner den endelige score for en liste af kandidater."""

    def __init__(self, config: RankingConfig | None = None) -> None:
        self.config = config or get_ranking_config()

    # -- Delscorer ----------------------------------------------------------

    def position_score(self, position: int | None) -> float:
        """Placering -> 0–1. Se modulets docstring."""
        if position is None or position < 1:
            return 0.0
        k = self.config.rank_decay_k
        return float(k / (k + position - 1))

    # -- Samlet score -------------------------------------------------------

    def score(self, signals: RankingSignals, intent: QueryIntent) -> RankingBreakdown:
        cfg = self.config
        weights = cfg.weights

        lexical = self.position_score(signals.lexical_position)
        semantic = self.position_score(signals.semantic_position)
        authority = float(signals.authority_score or 0.0)
        scope = float(signals.scope_score or 0.0)
        maritime = max(0.0, min(1.0, (signals.maritime_score or 0) / 100.0))
        status = cfg.status_score(signals.status)

        base = (
            weights["lexical"] * lexical
            + weights["semantic"] * semantic
            + weights["authority"] * authority
            + weights["scope"] * scope
            + weights["maritime_relevance"] * maritime
            + weights["status"] * status
        )

        adjustments = self._adjustments(signals, intent)
        multiplier = 1.0
        for adjustment in adjustments:
            multiplier *= adjustment.factor
        multiplier = max(float(cfg.domain_rules.get("min_multiplier", 0.2)), multiplier)

        return RankingBreakdown(
            document_id=signals.document_id,
            lexical_score=lexical,
            semantic_score=semantic,
            authority_score=authority,
            scope_score=scope,
            maritime_score=maritime,
            status_score=status,
            base_score=base,
            multiplier=multiplier,
            final_score=base * multiplier,
            adjustments=adjustments,
        )

    # -- Domæneregler -------------------------------------------------------

    def _adjustments(
        self, signals: RankingSignals, intent: QueryIntent
    ) -> list[RankingAdjustment]:
        cfg = self.config
        rules = cfg.rules_for(intent.kind)
        result: list[RankingAdjustment] = []

        law_class = signals.law_class or LawClass.CORE

        if law_class == LawClass.CORE:
            boost = float(rules.get("kernelaw_boost", 0.0))
            if boost:
                result.append(
                    RankingAdjustment(
                        "kernelaw_boost",
                        1.0 + boost,
                        f"Kernelov opjusteret ved {intent.label.lower()}",
                    )
                )

        elif law_class == LawClass.SPECIAL:
            if intent.is_niche:
                shared = set(signals.niche_groups) & set(intent.niche_groups)
                if shared:
                    boost = float(rules.get("matching_speciallaw_boost", 0.0))
                    if boost:
                        result.append(
                            RankingAdjustment(
                                "matching_speciallaw_boost",
                                1.0 + boost,
                                "Speciallov for netop den niche der søges på: "
                                + ", ".join(sorted(shared)),
                            )
                        )
                elif intent.niche_groups:
                    # Kun når søgningen selv peger på en ANDEN niche. Er
                    # søgningen blevet specifik alene fordi termen er
                    # sjælden (se `refine_intent`), er der ingen niche at
                    # være uenig med, og en straf ville ramme netop det
                    # dokument, brugeren ledte efter.
                    penalty = float(rules.get("unmatched_speciallaw_penalty", 0.0))
                    if penalty:
                        result.append(
                            RankingAdjustment(
                                "unmatched_speciallaw_penalty",
                                1.0 - penalty,
                                "Speciallov for et andet nicheområde end det der søges på",
                            )
                        )
            else:
                penalty = float(rules.get("speciallaw_penalty", 0.0))
                if penalty:
                    result.append(
                        RankingAdjustment(
                            "speciallaw_penalty",
                            1.0 - penalty,
                            f"Speciallov nedjusteret ved {intent.label.lower()}",
                        )
                    )

        elif law_class == LawClass.SUPPORT:
            penalty = float(rules.get("support_penalty", 0.0))
            if penalty:
                result.append(
                    RankingAdjustment(
                        "support_penalty",
                        1.0 - penalty,
                        "Støttedokument (vejledning, ændring eller historisk version)",
                    )
                )

        # Historisk og ophævet ret nedjusteres uanset klasse. Den er stadig
        # søgbar — og et eksplicit statusfilter fjerner nedjusteringen ikke,
        # men da er alle resultater historiske, og rækkefølgen indbyrdes
        # står uændret.
        folded_status = fold(signals.status or "")
        if folded_status in {"historisk", "ophaevet"}:
            penalty = float(cfg.domain_rules.get("historic_penalty", 0.0))
            if penalty:
                result.append(
                    RankingAdjustment(
                        "historic_penalty",
                        1.0 - penalty,
                        f"Ikke gældende ret ({signals.status})",
                    )
                )

        return result

    # -- Bekvemmelighed -----------------------------------------------------

    def rank(
        self, signals: list[RankingSignals], intent: QueryIntent
    ) -> list[RankingBreakdown]:
        """Scorer og sorterer. Stabil ved lige score: laveste dokument-id først."""
        scored = [self.score(item, intent) for item in signals]
        scored.sort(key=lambda b: (-b.final_score, b.document_id))
        return scored

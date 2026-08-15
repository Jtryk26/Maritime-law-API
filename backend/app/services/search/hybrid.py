"""Hybridsøgning: leksikalsk og semantisk smeltet sammen.

Hvorfor overhovedet smelte sammen
=================================
De to slags søgning fejler på hver sin måde, og de fejler sjældent
samtidig.

* Leksikalsk finder intet, når brugeren og loven bruger forskellige ord.
  Søger man "livbåd", finder man ikke "Bekendtgørelse om redningsmidler",
  fordi ordet ikke står der.
* Semantisk er upræcis, når ordvalget er præcist. Søger man "MARPOL
  bilag VI", vil man have netop de dokumenter — ikke alt der handler om
  luftforurening fra skibe.

Juridisk søgning har brug for begge dele, og det er derfor hybrid er
standardtilstanden.

Reciprocal Rank Fusion
======================
Sammensmeltningen bruger RRF frem for at lægge scorerne sammen::

    score(d) = w_leks / (k + rang_leks(d)) + w_sem / (k + rang_sem(d))

Grunden er, at de to scorer ikke er sammenlignelige størrelser.
``ts_rank_cd`` er et ubegrænset tal, der afhænger af dokumentets længde
og af hvor mange gange termen står der; cosinus-lighed ligger mellem 0
og 1 og er ret sammentrykt i den øvre ende. At normalisere dem til
samme skala kræver antagelser om fordelingen, som ikke holder. RRF ser
bort fra scorerne og bruger kun *rækkefølgen*, som er det eneste de to
metoder er enige om at måle.

``k = 60`` er den værdi der bruges i litteraturen; den dæmper hvor meget
en enkelt førsteplads kan trække.

Vægtene er konfigurerbare. Standard er leksikalsk 1,0 og semantisk 0,8 —
en tilsigtet overvægt til de eksakte ord, fordi en jurist der skriver en
paragrafhenvisning mener netop den.

Om `total` og sideinddeling
===========================
Hybridsøgning tæller **kandidater**, ikke hele databasen. Hver side
henter op til ``HYBRID_CANDIDATE_LIMIT`` dokumenter, og `total` er
størrelsen af foreningsmængden. Rammes loftet, sættes `truncated`, og
brugerfladen skriver "mindst N". Alternativet — at rangere hele
databasen ved hver søgning — ville koste en fuld vektorsammenligning pr.
forespørgsel uden at gøre de første sider bedre.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.text import make_snippet, tokenize

from .base import SearchBackend, SearchHit, SearchQuery, SearchResults
from .vector import VectorSearchBackend

logger = get_logger(__name__)

__all__ = ["HybridSearchBackend", "FusedHit"]


@dataclass(slots=True)
class FusedHit:
    """Ét dokument efter sammensmeltning."""

    document_id: int
    score: float
    lexical_rank: float | None = None
    lexical_position: int | None = None
    semantic_score: float | None = None
    semantic_position: int | None = None
    snippet: str = ""
    heading: str | None = None

    @property
    def match_source(self) -> str:
        if self.lexical_position is not None and self.semantic_position is not None:
            return "both"
        if self.semantic_position is not None:
            return "semantic"
        return "lexical"


class HybridSearchBackend:
    """Leksikalsk + semantisk med Reciprocal Rank Fusion."""

    name = "hybrid"

    def __init__(
        self,
        lexical: SearchBackend,
        vector: VectorSearchBackend,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.lexical = lexical
        self.vector = vector
        self.settings = settings or get_settings()

    # -- Kontrakt -----------------------------------------------------------

    def search(self, session: Session, query: SearchQuery) -> SearchResults:
        text = (query.q or "").strip()

        # Uden søgestreng er der intet at smelte sammen: det er en
        # filtreret liste, og den leverer den leksikalske backend bedst
        # (med korrekt total og fuld sideinddeling).
        if not text:
            results = self.lexical.search(session, query)
            results.mode = "lexical"
            results.semantic_available = True
            return results

        limit = self.settings.hybrid_candidate_limit

        lexical_hits = self._lexical_candidates(session, query, limit)
        try:
            semantic_matches = self.vector.match_documents(session, query, limit=limit)
        except Exception as exc:  # noqa: BLE001 - modellen må ikke kunne vælte en søgning
            logger.warning("search.hybrid.semantic_failed", extra={"error": str(exc)})
            results = self.lexical.search(session, query)
            results.mode = "lexical"
            results.notice = (
                "Betydningssøgning var ikke tilgængelig. Resultatet er leksikalsk."
            )
            return results

        fused = self._fuse(lexical_hits, semantic_matches)
        total = len(fused)
        page = fused[query.offset : query.offset + query.page_size]

        documents = {hit.document.id: hit.document for hit in lexical_hits}
        missing = [f.document_id for f in page if f.document_id not in documents]
        if missing:
            from .backends import _load_options
            from .vector import _documents_by_id

            documents.update(_documents_by_id(session, missing, _load_options()))

        terms = tokenize(text)
        hits: list[SearchHit] = []
        for fused_hit in page:
            document = documents.get(fused_hit.document_id)
            if document is None:
                continue
            snippet = fused_hit.snippet
            if not snippet:
                version = document.current_version
                snippet = make_snippet(version.content, terms) if version else ""
            hits.append(
                SearchHit(
                    document=document,
                    rank=fused_hit.score,
                    snippet=snippet,
                    lexical_rank=fused_hit.lexical_rank,
                    semantic_score=fused_hit.semantic_score,
                    match_source=fused_hit.match_source,
                    matched_heading=fused_hit.heading,
                )
            )

        truncated = len(lexical_hits) >= limit or len(semantic_matches) >= limit
        return SearchResults(
            hits=hits,
            total=total,
            page=query.page,
            page_size=query.page_size,
            backend=f"{self.name}:{self.lexical.name}",
            mode="hybrid",
            semantic_available=True,
            truncated=truncated,
        )

    # -- Delsøgninger -------------------------------------------------------

    def _lexical_candidates(
        self, session: Session, query: SearchQuery, limit: int
    ) -> list[SearchHit]:
        """Den leksikalske sides kandidater, uden sideinddeling.

        Der genbruges `search()` frem for at duplikere filtrering og
        rangering. Ét sted at rette, hvis et filter ændrer sig.
        """
        from dataclasses import replace

        candidate_query = replace(query, page=1, page_size=limit, sort="relevance")
        return self.lexical.search(session, candidate_query).hits

    # -- Sammensmeltning ----------------------------------------------------

    def _fuse(self, lexical_hits: list[SearchHit], semantic_matches: list) -> list[FusedHit]:
        k = self.settings.hybrid_rrf_k
        w_lexical = self.settings.hybrid_lexical_weight
        w_semantic = self.settings.hybrid_semantic_weight

        fused: dict[int, FusedHit] = {}

        for position, hit in enumerate(lexical_hits, start=1):
            fused[hit.document.id] = FusedHit(
                document_id=hit.document.id,
                score=w_lexical / (k + position),
                lexical_rank=hit.rank,
                lexical_position=position,
                snippet=hit.snippet,
            )

        for position, match in enumerate(semantic_matches, start=1):
            contribution = w_semantic / (k + position)
            existing = fused.get(match.document_id)
            if existing is None:
                fused[match.document_id] = FusedHit(
                    document_id=match.document_id,
                    score=contribution,
                    semantic_score=match.similarity,
                    semantic_position=position,
                    snippet=match.chunk.content[:400],
                    heading=match.chunk.heading,
                )
            else:
                existing.score += contribution
                existing.semantic_score = match.similarity
                existing.semantic_position = position
                existing.heading = existing.heading or match.chunk.heading
                # Er dokumentet fundet leksikalsk, beholdes det uddrag der
                # indeholder søgeordene — det er mere oplysende end det
                # semantisk nærmeste stykke.

        return sorted(
            fused.values(),
            # Ved lige score vinder den der stod bedst leksikalsk. Det gør
            # rækkefølgen deterministisk, hvilket testene er afhængige af.
            key=lambda f: (-f.score, f.lexical_position or 10**6, f.document_id),
        )

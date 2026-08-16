"""Domænejusteret søgning: leksikalsk og semantisk, rangeret som lovstof.

Hvad der er ændret i forhold til ren Reciprocal Rank Fusion
===========================================================
Den tidligere sammensmeltning gjorde ét: den flettede to lister sammen
efter placering. Den var korrekt, men den kendte kun tekst. Ved en bred
søgning som ``hviletid`` kunne en bekendtgørelse om hviletid for
grønlandske lodser derfor stå over hovedreglen for søfarende — fordi
ordet stod tættere på overfladen i det smalle dokument.

Rangeringen bruger nu seks signaler::

    final_score = ( 0.40 * lexical + 0.25 * semantic + 0.15 * authority
                  + 0.10 * scope   + 0.05 * maritime + 0.05 * status )
                  * domæneregler

Vægtene og reglerne står i ``config/ranking.yaml``.

Rangbaserede delscorer, ikke rå scorer
======================================
``ts_rank_cd`` og cosinus-lighed kan ikke lægges sammen som tal — det var
netop begrundelsen for RRF. Indsigten er bevaret: begge delsøgninger
bidrager med deres **placering**, omregnet til 0–1 med ``k/(k+n-1)``.
Fusionen er stadig rangbaseret; den har blot fået fire domænesignaler ved
siden af sig, og de kan lægges til, fordi de allerede er 0–1.

Forklarlighed
=============
Hver domæneregel er en multiplikator med en begrundelse i klartekst, som
følger med i svaret. Brugerfladen kan derfor sige "nedjusteret 30 % —
speciallov ved bred søgning" i stedet for at præsentere ét uigennemsigtigt
tal.

Paragraffen følger med
======================
For hvert resultat på den viste side findes den bedst matchende paragraf
med kapitelkontekst — i alle tre tilstande, også uden vektorer. Se
:mod:`app.services.search.paragraphs`.

Om `total` og sideinddeling
===========================
Med en søgestreng tælles **kandidater**, ikke hele databasen. Hver
delsøgning henter op til ``HYBRID_CANDIDATE_LIMIT`` dokumenter, og `total`
er størrelsen af foreningsmængden. Rammes loftet, sættes `truncated`, og
brugerfladen skriver "mindst N". Uden søgestreng er der intet at
sammensmelte: da er det en filtreret liste, og den leveres af den
leksikalske backend med korrekt total og fuld sideinddeling — sorteret
domænejusteret direkte i SQL.
"""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.text import make_snippet, tokenize
from app.models import Document, DocumentChunk
from app.services.ranking import (
    DomainRanker,
    LawClass,
    QueryIntent,
    RankingSignals,
    classify_query_intent,
    refine_intent,
)

from .base import SearchBackend, SearchHit, SearchQuery, SearchResults
from .paragraphs import ParagraphHit, locate_paragraphs
from .vector import VectorSearchBackend

logger = get_logger(__name__)

__all__ = ["RankedSearchBackend", "HybridSearchBackend"]

#: Hvor mange paragraffer der hentes pr. resultat. Den bedste vises;
#: resten kan foldes ud af brugeren ("vis flere paragraffer").
PARAGRAPHS_PER_HIT = 3


class RankedSearchBackend:
    """Leksikalsk + semantisk + domæneregler.

    Håndterer alle tre tilstande. `mode` afgør hvilke delsøgninger der
    køres; rangeringsmodellen er den samme, så en bruger der skifter fra
    "ordret" til "kombineret" ikke også får en anden opfattelse af, hvad
    der er en central lov.
    """

    def __init__(
        self,
        lexical: SearchBackend,
        vector: VectorSearchBackend | None = None,
        *,
        mode: str = "hybrid",
        settings: Settings | None = None,
        ranker: DomainRanker | None = None,
    ) -> None:
        self.lexical = lexical
        self.vector = vector
        self.mode = mode
        self.settings = settings or get_settings()
        self.ranker = ranker or DomainRanker()

    @property
    def name(self) -> str:
        return f"{self.mode}:{self.lexical.name}"

    # -- Kontrakt -----------------------------------------------------------

    def search(self, session: Session, query: SearchQuery) -> SearchResults:
        text = (query.q or "").strip()
        intent = classify_query_intent(text)

        # Uden søgestreng, eller når brugeren selv har valgt en sortering,
        # er der ikke to lister at rangere mellem. Den leksikalske backend
        # leverer da listen med korrekt total og fuld sideinddeling.
        if not text or query.sort != "relevance":
            results = self.lexical.search(session, query)
            results.mode = "lexical" if not text else self.mode
            results.semantic_available = self.vector is not None
            results.intent = intent
            if not text:
                results.mode = "lexical"
            self._attach_paragraphs(session, results, text)
            return results

        limit = self.settings.hybrid_candidate_limit

        lexical_hits = (
            self._lexical_candidates(session, query, limit)
            if self.mode in {"lexical", "hybrid"}
            else []
        )

        semantic_matches: list = []
        if self.mode in {"semantic", "hybrid"} and self.vector is not None:
            try:
                semantic_matches = self.vector.match_documents(session, query, limit=limit)
            except Exception as exc:  # noqa: BLE001 - modellen må ikke vælte en søgning
                logger.warning("search.ranked.semantic_failed", extra={"error": str(exc)})
                fallback = self.lexical.search(session, query)
                fallback.mode = "lexical"
                fallback.intent = intent
                fallback.notice = (
                    "Betydningssøgning var ikke tilgængelig. Resultatet er leksikalsk."
                )
                self._attach_paragraphs(session, fallback, text)
                return fallback

        # Ordvalget alene afgør ikke, om søgningen er bred. Er der kun et
        # par leksikalske træf, er termen sjælden, og søgningen er
        # specifik — se `refine_intent`.
        if self.mode in {"lexical", "hybrid"}:
            intent = refine_intent(intent, len(lexical_hits))

        documents = {hit.document.id: hit.document for hit in lexical_hits}
        signals = self._signals(session, lexical_hits, semantic_matches, documents)
        ranked = self.ranker.rank(list(signals.values()), intent)

        total = len(ranked)
        page = ranked[query.offset : query.offset + query.page_size]

        missing = [b.document_id for b in page if b.document_id not in documents]
        if missing:
            from .backends import _load_options
            from .vector import _documents_by_id

            documents.update(_documents_by_id(session, missing, _load_options()))

        semantic_by_document = {m.document_id: m for m in semantic_matches}
        lexical_by_document = {h.document.id: h for h in lexical_hits}
        terms = tokenize(text)

        hits: list[SearchHit] = []
        for breakdown in page:
            document = documents.get(breakdown.document_id)
            if document is None:
                continue
            lexical_hit = lexical_by_document.get(breakdown.document_id)
            semantic_hit = semantic_by_document.get(breakdown.document_id)

            snippet = lexical_hit.snippet if lexical_hit else ""
            if not snippet and semantic_hit is not None:
                snippet = make_snippet(semantic_hit.chunk.content, terms) or (
                    semantic_hit.chunk.content[:320]
                )
            if not snippet:
                version = document.current_version
                snippet = make_snippet(version.content, terms) if version else ""

            hits.append(
                SearchHit(
                    document=document,
                    rank=breakdown.final_score,
                    snippet=snippet,
                    lexical_rank=lexical_hit.lexical_rank if lexical_hit else None,
                    semantic_score=semantic_hit.similarity if semantic_hit else None,
                    match_source=signals[breakdown.document_id].match_source,
                    matched_heading=(
                        semantic_hit.chunk.heading if semantic_hit is not None else None
                    ),
                    ranking=breakdown,
                )
            )

        truncated = len(lexical_hits) >= limit or len(semantic_matches) >= limit
        results = SearchResults(
            hits=hits,
            total=total,
            page=query.page,
            page_size=query.page_size,
            backend=self.name,
            mode=self.mode,
            semantic_available=self.vector is not None,
            truncated=truncated,
            intent=intent,
        )
        self._attach_paragraphs(session, results, text)
        return results

    # -- Delsøgninger -------------------------------------------------------

    def _lexical_candidates(
        self, session: Session, query: SearchQuery, limit: int
    ) -> list[SearchHit]:
        """Den leksikalske sides kandidater, uden sideinddeling.

        Der genbruges `search()` frem for at duplikere filtrering og
        rangering. Ét sted at rette, hvis et filter ændrer sig.
        """
        candidate_query = replace(query, page=1, page_size=limit, sort="relevance")
        return self.lexical.search(session, candidate_query).hits

    # -- Signaler -----------------------------------------------------------

    def _signals(
        self,
        session: Session,
        lexical_hits: list[SearchHit],
        semantic_matches: list,
        documents: dict[int, Document],
    ) -> dict[int, RankingSignals]:
        """Ét signalsæt pr. kandidatdokument.

        For leksikalske kandidater har vi allerede hele dokumentet. For
        dokumenter, der kun blev fundet semantisk, hentes de fem
        rangeringsfelter i én forespørgsel — ikke hele rækken med
        kategorier og version, som først er nødvendig for den viste side.
        """
        signals: dict[int, RankingSignals] = {}

        for position, hit in enumerate(lexical_hits, start=1):
            document = hit.document
            signals[document.id] = RankingSignals(
                document_id=document.id,
                lexical_position=position,
                law_class=document.law_class or LawClass.CORE,
                scope_score=document.scope_score if document.scope_score is not None else 0.55,
                authority_score=(
                    document.authority_score if document.authority_score is not None else 0.5
                ),
                maritime_score=document.maritime_score or 0,
                status=document.status,
                niche_groups=list(document.niche_groups or []),
            )

        unknown = [
            match.document_id
            for match in semantic_matches
            if match.document_id not in signals
        ]
        rows = _ranking_rows(session, unknown) if unknown else {}

        for position, match in enumerate(semantic_matches, start=1):
            existing = signals.get(match.document_id)
            if existing is not None:
                existing.semantic_position = position
                continue
            row = rows.get(match.document_id)
            if row is None:
                continue
            signals[match.document_id] = RankingSignals(
                document_id=match.document_id,
                semantic_position=position,
                law_class=row["law_class"] or LawClass.CORE,
                scope_score=row["scope_score"] if row["scope_score"] is not None else 0.55,
                authority_score=(
                    row["authority_score"] if row["authority_score"] is not None else 0.5
                ),
                maritime_score=row["maritime_score"] or 0,
                status=row["status"],
                niche_groups=list(row["niche_groups"] or []),
            )

        return signals

    # -- Paragraffer --------------------------------------------------------

    def _attach_paragraphs(self, session: Session, results: SearchResults, text: str) -> None:
        """Sætter den bedst matchende paragraf på hvert resultat.

        Sker efter sideinddelingen, så der højst parses `page_size`
        dokumenter — og kun deres gældende version.
        """
        if not results.hits:
            return
        located = locate_paragraphs(
            session,
            [hit.document for hit in results.hits],
            tokenize(text),
            per_document=PARAGRAPHS_PER_HIT,
        )
        for hit in results.hits:
            found = located.get(hit.document.id) or []
            if not found:
                # Rent semantisk hit: brugerens ord står ikke i teksten, så
                # der er ingen paragraf at pege på ved ordmatch. Til gengæld
                # ved vektorindekset præcis hvilket stykke der lignede, og
                # dets juridiske adresse står i `document_chunks`.
                fallback = self._paragraph_from_chunk(session, hit)
                if fallback is not None:
                    hit.paragraph = fallback
                continue
            hit.paragraph = found[0]
            hit.paragraphs = found[1:]
            if not hit.matched_heading:
                hit.matched_heading = found[0].legal_path

    def _paragraph_from_chunk(self, session: Session, hit: SearchHit) -> ParagraphHit | None:
        """Paragrafoplysninger fra det semantisk bedst matchende stykke."""
        if hit.semantic_score is None or self.vector is None:
            return None
        stmt = select(DocumentChunk).where(
            DocumentChunk.document_id == hit.document.id,
            DocumentChunk.paragraph_id.is_not(None),
        )
        if hit.matched_heading:
            stmt = stmt.where(DocumentChunk.heading == hit.matched_heading)
        chunk = session.scalars(stmt.order_by(DocumentChunk.chunk_index).limit(1)).first()
        if chunk is None:
            return None
        return ParagraphHit(
            document_id=hit.document.id,
            paragraph_id=chunk.paragraph_id or "",
            chapter_no=chunk.chapter_no,
            chapter_title=chunk.chapter_title,
            section_title=chunk.section_title,
            legal_path=chunk.heading or (chunk.paragraph_id or ""),
            full_citation=chunk.full_citation or "",
            snippet=(chunk.content or "")[:320],
            score=0.0,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
        )


def _ranking_rows(session: Session, ids: list[int]) -> dict[int, dict]:
    """Rangeringsfelterne for en liste dokumenter, uden at hente hele rækken."""
    if not ids:
        return {}
    rows = session.execute(
        select(
            Document.id,
            Document.law_class,
            Document.scope_score,
            Document.authority_score,
            Document.maritime_score,
            Document.status,
            Document.niche_groups,
        ).where(Document.id.in_(ids))
    ).all()
    return {
        row[0]: {
            "law_class": row[1],
            "scope_score": row[2],
            "authority_score": row[3],
            "maritime_score": row[4],
            "status": row[5],
            "niche_groups": row[6],
        }
        for row in rows
    }


#: Bevaret navn. Klassen gør nu mere end at smelte to lister sammen, men
#: den er stadig den backend, "hybrid" peger på, og navnet bruges i tests
#: og i evalueringsværktøjet.
HybridSearchBackend = RankedSearchBackend

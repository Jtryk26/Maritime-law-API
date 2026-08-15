"""Betydningssøgning på vektoriserede stykker lovtekst.

Sådan finder den frem
=====================
Søgestrengen vektoriseres med samme model som lovteksten. Derefter
findes de stykker hvis vektor ligger tættest på, og et dokuments score
er ligheden med **dets bedst matchende stykke** — ikke gennemsnittet.
Gennemsnittet ville straffe en lang bekendtgørelse, hvor kun én paragraf
handler om det man søger, og det er netop den paragraf man skal have.

To veje til de nærmeste stykker
===============================
``pgvector`` (produktion)
    ``ORDER BY embedding_vec <=> :vektor`` med HNSW-indeks. Databasen
    gør arbejdet, og responstiden holder på hundredtusindvis af stykker.

``portabel brute force`` (SQLite, eller PostgreSQL uden pgvector)
    Vektorerne læses ind og sammenlignes i numpy. Fuldstændig korrekt,
    men arbejdet vokser lineært. Derfor et loft,
    ``VECTOR_FALLBACK_MAX_CHUNKS``: over det antal advarer den og
    behandler kun de første. Bedre et højlydt undertal end en søgning,
    der tager tyve sekunder.

Filtrene gælder også her
========================
Kategori, myndighed, status, dato og maritim score anvendes gennem
nøjagtigt samme `_apply_filters` som den leksikalske søgning. Et filter
findes kun ét sted, ellers ville de to sider af hybridsøgningen kunne
komme til at filtrere forskelligt.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import Float, cast, literal, literal_column, select
from sqlalchemy.orm import Session
from sqlalchemy.types import UserDefinedType

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.text import make_snippet, tokenize
from app.core.vectors import cosine_similarity, to_pgvector_literal, unpack_matrix
from app.db.vector_support import has_pgvector
from app.models import Document, DocumentChunk
from app.services.embedding import EmbeddingProvider, get_embedding_provider

from .base import ScoredChunk, SearchHit, SearchQuery, SearchResults

logger = get_logger(__name__)

__all__ = ["VectorSearchBackend", "DocumentMatch", "PgVector"]


class PgVector(UserDefinedType):
    """pgvector's ``vector``-type, kun til CAST i forespørgsler.

    Vi tager ikke afhængighed af pgvector's Python-pakke: den eneste
    grænseflade vi bruger er ``CAST(:tekst AS vector)``, og en
    bundet tekstparameter er både nok og sikrere end en interpoleret
    streng. Kolonnen selv står ikke i modellen, fordi den kun findes på
    PostgreSQL og oprettes betinget i migration 0004.
    """

    cache_ok = True

    def get_col_spec(self, **kwargs) -> str:  # noqa: D102 - SQLAlchemy-kontrakt
        return "vector"


class DocumentMatch:
    """Et dokument og dets bedste stykke."""

    __slots__ = ("document_id", "similarity", "chunk")

    def __init__(self, document_id: int, similarity: float, chunk: ScoredChunk) -> None:
        self.document_id = document_id
        self.similarity = similarity
        self.chunk = chunk


class VectorSearchBackend:
    """Semantisk søgning."""

    name = "vector"

    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._provider = provider

    @property
    def min_similarity(self) -> float:
        """Den grænse der faktisk bruges.

        Konfigurationen vinder, hvis den er sat. Ellers spørges udbyderen,
        som er det eneste sted der kender modellens skala.
        """
        configured = self.settings.vector_min_similarity
        if configured is not None:
            return float(configured)
        return float(getattr(self.provider.info, "suggested_min_similarity", 0.0))

    @property
    def provider(self) -> EmbeddingProvider:
        # Hentes doven, så en fejlkonfigureret model først mærkes når
        # nogen faktisk søger semantisk — ikke ved opstart af API'et.
        if self._provider is None:
            self._provider = get_embedding_provider()
        return self._provider

    # -- Kandidater ---------------------------------------------------------

    def has_vectors(self, session: Session) -> bool:
        """Findes der overhovedet vektorer med den aktuelle model?"""
        model = self.provider.info.model
        found = session.execute(
            select(DocumentChunk.id)
            .where(DocumentChunk.embedding_model == model)
            .limit(1)
        ).first()
        return found is not None

    def match_documents(
        self,
        session: Session,
        query: SearchQuery,
        *,
        limit: int,
        exclude_document_id: int | None = None,
        query_vector: np.ndarray | None = None,
    ) -> list[DocumentMatch]:
        """De bedst matchende dokumenter, ét match pr. dokument."""
        text = (query.q or "").strip()
        if query_vector is None:
            if not text:
                return []
            query_vector = self.provider.embed_query(text)

        # Chunk-loftet sættes højere end dokumentloftet: flere stykker af
        # samme dokument kan sagtens ligge i toppen, og så ville et
        # dokumentloft på 200 stykker give langt færre end 200 dokumenter.
        chunk_limit = max(limit * 5, 100)

        if has_pgvector(session):
            chunks = self._chunks_pgvector(
                session, query, query_vector, chunk_limit, exclude_document_id
            )
        else:
            chunks = self._chunks_bruteforce(
                session, query, query_vector, chunk_limit, exclude_document_id
            )

        threshold = self.min_similarity
        best: dict[int, DocumentMatch] = {}
        for chunk in chunks:
            if chunk.similarity < threshold:
                continue
            current = best.get(chunk.document_id)
            if current is None or chunk.similarity > current.similarity:
                best[chunk.document_id] = DocumentMatch(
                    chunk.document_id, chunk.similarity, chunk
                )

        matches = sorted(best.values(), key=lambda m: m.similarity, reverse=True)
        return matches[:limit]

    # -- De to veje ---------------------------------------------------------

    def _filtered_document_ids(self, query: SearchQuery, exclude_document_id: int | None):
        """Underforespørgsel med de dokumenter filtrene tillader."""
        from .backends import _apply_filters  # cirkulær import undgås ved sen import

        stmt = select(Document.id)
        stmt = _apply_filters(stmt, query)
        if exclude_document_id is not None:
            stmt = stmt.where(Document.id != exclude_document_id)
        return stmt

    def _chunks_pgvector(
        self,
        session: Session,
        query: SearchQuery,
        vector: np.ndarray,
        limit: int,
        exclude_document_id: int | None,
    ) -> list[ScoredChunk]:
        allowed = self._filtered_document_ids(query, exclude_document_id)

        # Kolonnen findes kun på PostgreSQL og er derfor ikke i modellen.
        # literal_column (ikke text()) fordi kun et kolonneudtryk kan bære
        # operatoren <=> — samme greb som `search_vector` i backends.py.
        column = literal_column("document_chunks.embedding_vec")
        parameter = cast(literal(to_pgvector_literal(vector)), PgVector())
        # <=> er cosinus-AFSTAND. Mindre er bedre, derfor ORDER BY på den
        # og 1 - afstand som den lighed vi rapporterer.
        distance = column.op("<=>", return_type=Float)(parameter)
        similarity = (literal(1.0) - distance).label("similarity")

        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.heading,
                DocumentChunk.content,
                similarity,
            )
            .where(
                column.is_not(None),
                DocumentChunk.embedding_model == self.provider.info.model,
                DocumentChunk.document_id.in_(allowed),
            )
            .order_by(distance)
            .limit(limit)
        )

        return [
            ScoredChunk(
                chunk_id=row[0],
                document_id=row[1],
                heading=row[2],
                content=row[3] or "",
                similarity=float(row[4] or 0.0),
            )
            for row in session.execute(stmt).all()
        ]

    def _chunks_bruteforce(
        self,
        session: Session,
        query: SearchQuery,
        vector: np.ndarray,
        limit: int,
        exclude_document_id: int | None,
    ) -> list[ScoredChunk]:
        allowed = self._filtered_document_ids(query, exclude_document_id)
        cap = self.settings.vector_fallback_max_chunks

        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.heading,
                DocumentChunk.content,
                DocumentChunk.embedding,
            )
            .where(
                DocumentChunk.embedding.is_not(None),
                DocumentChunk.embedding_model == self.provider.info.model,
                DocumentChunk.document_id.in_(allowed),
            )
            .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
            .limit(cap + 1)
        )

        rows = session.execute(stmt).all()
        if len(rows) > cap:
            logger.warning(
                "vector.fallback.truncated",
                extra={
                    "cap": cap,
                    "note": (
                        "Flere stykker end den portable vektorsøgning kan nå. "
                        "Kør PostgreSQL med pgvector for fuld dækning."
                    ),
                },
            )
            rows = rows[:cap]

        if not rows:
            return []

        dimensions = self.provider.info.dimensions
        matrix = unpack_matrix((row[4] for row in rows), dimensions)
        similarities = cosine_similarity(np.asarray(vector, dtype=np.float32), matrix)

        # Kun de bedste `limit` stykker skal bygges som objekter.
        top = np.argsort(-similarities)[:limit]
        return [
            ScoredChunk(
                chunk_id=rows[i][0],
                document_id=rows[i][1],
                heading=rows[i][2],
                content=rows[i][3] or "",
                similarity=float(similarities[i]),
            )
            for i in top
        ]

    # -- Kontrakt -----------------------------------------------------------

    def search(self, session: Session, query: SearchQuery) -> SearchResults:
        """Ren semantisk søgning, med sideinddeling over kandidaterne."""
        from .backends import _load_options

        text = (query.q or "").strip()
        limit = self.settings.hybrid_candidate_limit

        if not text:
            # Uden søgestreng er der ingenting at ligne. Den leksikalske
            # backend håndterer "vis alt med disse filtre" langt bedre.
            from .backends import get_lexical_backend

            results = get_lexical_backend(session).search(session, query)
            results.mode = "lexical"
            results.notice = "Betydningssøgning kræver en søgestreng. Viste liste er filtreret."
            return results

        matches = self.match_documents(session, query, limit=limit)
        total = len(matches)
        page_matches = matches[query.offset : query.offset + query.page_size]

        documents = _documents_by_id(
            session, [m.document_id for m in page_matches], _load_options()
        )
        terms = tokenize(text)

        hits: list[SearchHit] = []
        for match in page_matches:
            document = documents.get(match.document_id)
            if document is None:
                continue
            hits.append(
                SearchHit(
                    document=document,
                    rank=match.similarity,
                    snippet=make_snippet(match.chunk.content, terms) or match.chunk.content[:280],
                    lexical_rank=None,
                    semantic_score=match.similarity,
                    match_source="semantic",
                    matched_heading=match.chunk.heading,
                )
            )

        return SearchResults(
            hits=hits,
            total=total,
            page=query.page,
            page_size=query.page_size,
            backend=self.name,
            mode="semantic",
            semantic_available=True,
            truncated=total >= limit,
        )

    # -- Lignende dokumenter ------------------------------------------------

    def similar_to_document(
        self, session: Session, document_id: int, *, limit: int = 10
    ) -> list[DocumentMatch]:
        """Dokumenter der ligner et givet dokument.

        Bruger dokumentets egne chunk-vektorer, ikke en ny beregning:
        gennemsnittet af stykkerne er dokumentets samlede "retning" i
        vektorrummet, og det er billigt og stabilt.
        """
        rows = session.execute(
            select(DocumentChunk.embedding).where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.embedding.is_not(None),
                DocumentChunk.embedding_model == self.provider.info.model,
            )
        ).all()
        if not rows:
            return []

        matrix = unpack_matrix((row[0] for row in rows), self.provider.info.dimensions)
        centroid = matrix.mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm == 0.0:
            return []
        centroid = (centroid / norm).astype(np.float32)

        # Kun maritim status arves; øvrige filtre giver ikke mening her.
        query = SearchQuery(q=None, page=1, page_size=limit)
        return self.match_documents(
            session,
            query,
            limit=limit,
            exclude_document_id=document_id,
            query_vector=centroid,
        )


def _documents_by_id(session: Session, ids: list[int], options) -> dict[int, Document]:
    """Henter dokumenter i én forespørgsel og bevarer opslag pr. id."""
    if not ids:
        return {}
    rows = session.scalars(
        select(Document).options(*options).where(Document.id.in_(ids))
    ).all()
    return {document.id: document for document in rows}

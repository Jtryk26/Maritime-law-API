"""Søgeloggen: hvad der bliver spurgt om, og hvad det ligner.

Hvad den er til
===============
Hver søgning gemmes én gang — aggregeret pr. normaliseret søgestreng —
sammen med sin egen vektor. Det giver tre ting med det samme:

* **Relaterede søgninger.** "Andre har også søgt efter ..." bygger på
  lighed mellem søgevektorer, ikke på fælles ord. "Livbåde" og
  "redningsflåder" hænger derfor sammen, selv om de intet ord deler.
* **Søgninger uden svar.** En søgning der er stillet 40 gange og aldrig
  har givet et resultat er den mest brugbare oplysning systemet kan give
  om hvad der mangler — enten i indekset eller i ordvalget.
* **Grundlaget for senere spørgsmål/svar.** Når systemet en dag skal
  besvare spørgsmål over lovteksten, er en samling rigtige spørgsmål med
  vektorer allerede det materiale, den funktion skal evalueres på.

Hvad den IKKE er
================
Der gemmes ingen bruger, ingen IP-adresse, ingen session og intet
tidsstempel pr. hændelse — kun første og seneste forekomst. Tabellen kan
besvare "hvad søger folk efter", ikke "hvem søgte hvad". Det er et
bevidst valg, ikke en forglemmelse.

Fejl her må aldrig ramme søgningen
==================================
Logningen sker efter at resultatet er fundet, og alle fejl fanges. En
database der ikke kan skrive, eller en model der ikke kan indlæses, skal
koste os en logpost — ikke brugerens søgeresultat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select, text as sql_text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.text import content_hash, fold, normalize_whitespace
from app.core.vectors import cosine_similarity, pack_vector, to_pgvector_literal, unpack_matrix
from app.db.vector_support import has_pgvector
from app.models import SearchQueryLog
from app.services.embedding import EmbeddingProvider, get_embedding_provider

logger = get_logger(__name__)

__all__ = ["QueryLogService", "RelatedQuery", "normalize_query"]


def normalize_query(text: str) -> str:
    """Den form to søgninger sammenlignes på.

    Foldning (æ->ae, ø->oe, små bogstaver) er samme behandling som
    søgeindekset får, så "Søulykke" og "soeulykke" bliver til én post og
    ikke to.
    """
    return fold(normalize_whitespace(text or "")).strip()


@dataclass(slots=True)
class RelatedQuery:
    """En tidligere søgning der ligner en anden."""

    query: str
    similarity: float
    occurrences: int
    last_result_count: int

    def to_json(self) -> dict:
        return {
            "query": self.query,
            "similarity": round(self.similarity, 4),
            "occurrences": self.occurrences,
            "last_result_count": self.last_result_count,
        }


class QueryLogService:
    """Skriver og læser søgeloggen."""

    def __init__(
        self,
        session: Session,
        provider: EmbeddingProvider | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._provider = provider
        self._provider_failed = False

    @property
    def provider(self) -> EmbeddingProvider | None:
        """Udbyderen, eller None hvis den ikke kan skaffes.

        En manglende model gør ikke logningen ubrugelig: teksten og
        tælleren er stadig værd at have. Kun ligheden må undvære.
        """
        if self._provider is not None or self._provider_failed:
            return self._provider
        if not self.settings.embeddings_enabled:
            self._provider_failed = True
            return None
        try:
            self._provider = get_embedding_provider()
        except Exception as exc:  # noqa: BLE001
            logger.info("query_log.provider_unavailable", extra={"error": str(exc)})
            self._provider_failed = True
        return self._provider

    # -- Skrivning ----------------------------------------------------------

    def record(
        self,
        query_text: str,
        *,
        result_count: int,
        mode: str,
    ) -> SearchQueryLog | None:
        """Registrerer en søgning. Returnerer posten, eller None hvis sprunget over.

        Kalder man to gange med samme streng, opstår der ikke to rækker:
        hashen af den normaliserede form er unik.
        """
        if not self.settings.search_query_log_enabled:
            return None

        text = (query_text or "").strip()
        normalized = normalize_query(text)
        if len(normalized) < self.settings.search_query_log_min_chars:
            return None

        try:
            return self._record(text, normalized, result_count=result_count, mode=mode)
        except Exception as exc:  # noqa: BLE001 - må aldrig ramme søgeresultatet
            self.session.rollback()
            logger.warning("query_log.record_failed", extra={"error": str(exc)})
            return None

    def _record(
        self, text: str, normalized: str, *, result_count: int, mode: str
    ) -> SearchQueryLog:
        digest = content_hash(normalized)
        now = datetime.now(timezone.utc)

        entry = self.session.scalars(
            select(SearchQueryLog).where(SearchQueryLog.query_hash == digest)
        ).first()

        if entry is None:
            entry = SearchQueryLog(
                query_hash=digest,
                query_text=text,
                normalized_query=normalized,
                occurrences=1,
                last_result_count=result_count,
                best_result_count=result_count,
                last_mode=mode,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(entry)
        else:
            entry.occurrences += 1
            entry.last_result_count = result_count
            entry.best_result_count = max(entry.best_result_count, result_count)
            entry.last_mode = mode
            entry.last_seen_at = now

        self._ensure_embedding(entry)
        self.session.flush()
        self.session.commit()

        logger.debug(
            "query_log.recorded",
            extra={
                "occurrences": entry.occurrences,
                "results": result_count,
                "mode": mode,
            },
        )
        return entry

    def _ensure_embedding(self, entry: SearchQueryLog) -> None:
        """Vektoriserer søgningen, hvis den mangler eller er fra en anden model."""
        provider = self.provider
        if provider is None:
            return

        model = provider.info.model
        if entry.embedding is not None and entry.embedding_model == model:
            return

        try:
            vector = provider.embed_query(entry.query_text)
        except Exception as exc:  # noqa: BLE001
            logger.info("query_log.embed_failed", extra={"error": str(exc)})
            return

        entry.embedding = pack_vector(vector)
        entry.embedding_model = model
        entry.embedding_dim = provider.info.dimensions

        if has_pgvector(self.session):
            self.session.flush()
            self.session.execute(
                sql_text(
                    "UPDATE search_queries SET embedding_vec = CAST(:vec AS vector) "
                    "WHERE id = :id"
                ),
                {"vec": to_pgvector_literal(vector), "id": entry.id},
            )

    # -- Læsning ------------------------------------------------------------

    def related(
        self, query_text: str, *, limit: int = 5, include_self: bool = False
    ) -> list[RelatedQuery]:
        """Tidligere søgninger der ligner denne.

        Brute force over søgeloggen. Det er forsvarligt her, hvor
        tabellen tæller søgestrenge og ikke dokumentstykker — selv et
        travlt system når sjældent over nogle tusinde forskellige
        søgninger, og de fylder få megabyte.
        """
        provider = self.provider
        if provider is None:
            return []

        normalized = normalize_query(query_text)
        if not normalized:
            return []

        rows = self.session.execute(
            select(
                SearchQueryLog.query_text,
                SearchQueryLog.normalized_query,
                SearchQueryLog.occurrences,
                SearchQueryLog.last_result_count,
                SearchQueryLog.embedding,
            ).where(
                SearchQueryLog.embedding.is_not(None),
                SearchQueryLog.embedding_model == provider.info.model,
            )
        ).all()
        if not rows:
            return []

        try:
            vector = provider.embed_query(query_text)
        except Exception as exc:  # noqa: BLE001
            logger.info("query_log.related_embed_failed", extra={"error": str(exc)})
            return []

        matrix = unpack_matrix((row[4] for row in rows), provider.info.dimensions)
        similarities = cosine_similarity(np.asarray(vector, dtype=np.float32), matrix)
        threshold = self.settings.related_query_min_similarity

        results: list[RelatedQuery] = []
        for index in np.argsort(-similarities):
            similarity = float(similarities[index])
            if similarity < threshold:
                break
            if not include_self and rows[index][1] == normalized:
                continue
            results.append(
                RelatedQuery(
                    query=rows[index][0],
                    similarity=similarity,
                    occurrences=int(rows[index][2]),
                    last_result_count=int(rows[index][3]),
                )
            )
            if len(results) >= limit:
                break

        return results

    def popular(self, *, limit: int = 10) -> list[SearchQueryLog]:
        """De hyppigste søgninger, nyeste først ved lige antal."""
        return list(
            self.session.scalars(
                select(SearchQueryLog)
                .order_by(SearchQueryLog.occurrences.desc(), SearchQueryLog.last_seen_at.desc())
                .limit(limit)
            ).all()
        )

    def without_results(self, *, limit: int = 20) -> list[SearchQueryLog]:
        """Søgninger der aldrig har givet et resultat.

        Den vigtigste liste i tabellen: enten mangler materialet, eller
        også taler brugerne et andet sprog end lovteksten. Begge dele er
        noget man vil vide.
        """
        return list(
            self.session.scalars(
                select(SearchQueryLog)
                .where(SearchQueryLog.best_result_count == 0)
                .order_by(SearchQueryLog.occurrences.desc(), SearchQueryLog.last_seen_at.desc())
                .limit(limit)
            ).all()
        )

    def stats(self) -> dict:
        total = self.session.scalar(select(func.count()).select_from(SearchQueryLog)) or 0
        searches = self.session.scalar(select(func.sum(SearchQueryLog.occurrences))) or 0
        empty = self.session.scalar(
            select(func.count())
            .select_from(SearchQueryLog)
            .where(SearchQueryLog.best_result_count == 0)
        ) or 0
        embedded = self.session.scalar(
            select(func.count())
            .select_from(SearchQueryLog)
            .where(SearchQueryLog.embedding.is_not(None))
        ) or 0
        return {
            "distinct_queries": total,
            "total_searches": int(searches),
            "queries_without_results": empty,
            "vectorized_queries": embedded,
        }

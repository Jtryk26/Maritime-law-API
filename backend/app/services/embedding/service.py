"""Indeksering: fra lovtekst til vektorer.

Ansvarsdeling
=============
Importeren henter og gemmer dokumenter. Den vektoriserer dem **ikke**.
Det er et bevidst valg:

* En import af 2.900 dokumenter ville tage timer længere, hvis hver
  tekst skulle gennem en CPU-model undervejs.
* En import må ikke fejle, fordi en embedding-model ikke kunne indlæses.
  Lovteksten er det vigtige; vektorerne er et indeks over den.
* Vektorer skal kunne bygges om alene — ved modelskifte, ved ændret
  chunk-størrelse — uden at røre kilden.

Derfor er indekseringen et selvstændigt trin::

    python -m app.cli import ...     # henter og gemmer
    python -m app.cli embed run      # vektoriserer det der mangler

Hvad "mangler" betyder, afgøres af `Document.needs_embedding`: aldrig
vektoriseret, ny version siden sidst, eller vektorer fra en anden model.
Ingen tilstandsmaskine, intet der kan komme ud af trit.

Idempotens
==========
Et dokument der indekseres igen får sine chunks slettet og skrevet på
ny. Det er billigere og langt mere forudsigeligt end at forsøge at
afgøre hvilke stykker der flyttede sig, da et enkelt indskudt ord kan
forskyde alle efterfølgende grænser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, text as sql_text, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.vectors import pack_vector, to_pgvector_literal
from app.db.vector_support import has_pgvector
from app.models import Document, DocumentChunk, DocumentVersion

from .base import EmbeddingError, EmbeddingProvider
from .chunking import ChunkingConfig, chunk_document

logger = get_logger(__name__)

__all__ = ["EmbeddingIndexer", "IndexingReport", "chunking_config_from_settings"]


@dataclass(slots=True)
class IndexingReport:
    """Resultatet af en indekseringskørsel."""

    documents_checked: int = 0
    documents_embedded: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    chunks_written: int = 0
    chunks_deleted: int = 0
    model: str = ""
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "documents_checked": self.documents_checked,
            "documents_embedded": self.documents_embedded,
            "documents_skipped": self.documents_skipped,
            "documents_failed": self.documents_failed,
            "chunks_written": self.chunks_written,
            "chunks_deleted": self.chunks_deleted,
            "model": self.model,
            "errors": self.errors[:20],
        }


def chunking_config_from_settings(settings: Settings | None = None) -> ChunkingConfig:
    cfg = settings or get_settings()
    return ChunkingConfig(
        target_chars=cfg.chunk_target_chars,
        max_chars=cfg.chunk_max_chars,
        overlap_chars=cfg.chunk_overlap_chars,
        min_chars=cfg.chunk_min_chars,
        max_per_document=cfg.chunk_max_per_document,
    )


class EmbeddingIndexer:
    """Bygger og vedligeholder det semantiske indeks."""

    def __init__(
        self,
        session: Session,
        provider: EmbeddingProvider,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self.chunking = chunking_config_from_settings(self.settings)
        self._use_pgvector = has_pgvector(session)

    # -- Udvælgelse ---------------------------------------------------------

    def pending_query(self, *, only_maritime: bool = True):
        """Dokumenter hvis vektorer mangler eller er forældede."""
        model = self.provider.info.model
        stmt = select(Document).where(Document.current_version_id.is_not(None))
        if only_maritime:
            # Ikke-maritime dokumenter gemmes for gennemsigtighedens skyld,
            # men de indgår ikke i den maritime søgning, og at vektorisere
            # dem ville koste tid uden at gøre nogen søgning bedre.
            stmt = stmt.where(Document.is_maritime.is_(True))
        return stmt.where(
            (Document.embedded_version_id.is_(None))
            | (Document.embedded_version_id != Document.current_version_id)
            | (func.coalesce(Document.embedding_model, "") != model)
        )

    def pending_count(self, *, only_maritime: bool = True) -> int:
        stmt = self.pending_query(only_maritime=only_maritime)
        return self.session.scalar(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        ) or 0

    # -- Indeksering --------------------------------------------------------

    def index_pending(
        self,
        *,
        limit: int | None = None,
        only_maritime: bool = True,
        reset: bool = False,
    ) -> IndexingReport:
        """Vektoriserer de dokumenter der mangler.

        `reset` sletter alle eksisterende chunks først og bygger forfra.
        Bruges ved modelskifte eller ændrede chunk-parametre.
        """
        report = IndexingReport(model=self.provider.info.model)

        if reset:
            report.chunks_deleted += self._delete_all_chunks()

        stmt = self.pending_query(only_maritime=only_maritime).order_by(Document.id)
        if limit is not None:
            stmt = stmt.limit(limit)

        documents = list(self.session.scalars(stmt).all())
        logger.info(
            "embedding.index.started",
            extra={
                "documents": len(documents),
                "model": self.provider.info.model,
                "semantic": self.provider.info.semantic,
                "pgvector": self._use_pgvector,
            },
        )

        for document in documents:
            report.documents_checked += 1
            try:
                written, deleted = self.index_document(document)
            except EmbeddingError:
                # Modellen er væk eller miskonfigureret. Det rammer alle
                # dokumenter ens, så der er intet at vinde ved at fortsætte.
                self.session.rollback()
                raise
            except Exception as exc:  # noqa: BLE001 - ét skævt dokument må ikke vælte kørslen
                self.session.rollback()
                report.documents_failed += 1
                message = f"{document.source_id}: {exc}"
                report.errors.append(message)
                logger.warning(
                    "embedding.index.document_failed",
                    extra={"source_id": document.source_id, "error": str(exc)},
                )
                continue

            report.chunks_written += written
            report.chunks_deleted += deleted
            if written:
                report.documents_embedded += 1
            else:
                report.documents_skipped += 1
            self.session.commit()

        logger.info("embedding.index.completed", extra=report.to_json())
        return report

    def index_document(self, document: Document) -> tuple[int, int]:
        """Vektoriserer ét dokument. Returnerer (skrevne, slettede) chunks."""
        version = (
            self.session.get(DocumentVersion, document.current_version_id)
            if document.current_version_id
            else None
        )
        content = version.content if version else ""

        deleted = self._delete_chunks(document.id)
        chunks = chunk_document(content, self.chunking)

        if not chunks:
            # Dokument uden brugbar tekst. Markeres alligevel som
            # behandlet, ellers ville hver kørsel forsøge igen i det
            # uendelige.
            document.embedded_version_id = document.current_version_id
            document.embedding_model = self.provider.info.model
            document.embedded_at = datetime.now(timezone.utc)
            document.chunk_count = 0
            self.session.flush()
            return 0, deleted

        # Titel, nummer og lovadresse med i den tekst der vektoriseres —
        # se `TextChunk.embedding_text`.
        texts = [
            chunk.embedding_text(document.title, document.document_number)
            for chunk in chunks
        ]
        vectors = self.provider.embed_passages(texts)

        model = self.provider.info.model
        dimensions = self.provider.info.dimensions
        rows: list[DocumentChunk] = []

        for chunk, vector in zip(chunks, vectors, strict=True):
            rows.append(
                DocumentChunk(
                    document_id=document.id,
                    version_id=version.id if version else None,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    # Den fulde adresse gemmes, ikke kun den nærmeste
                    # overskrift: brugerfladen skal kunne sige "Kapitel 3 · § 12".
                    heading=chunk.legal_path or chunk.heading,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    embedding=pack_vector(vector),
                    embedding_model=model,
                    embedding_dim=dimensions,
                )
            )

        self.session.add_all(rows)
        self.session.flush()

        if self._use_pgvector:
            self._write_pgvector(rows, vectors)

        document.embedded_version_id = document.current_version_id
        document.embedding_model = model
        document.embedded_at = datetime.now(timezone.utc)
        document.chunk_count = len(rows)
        self.session.flush()

        logger.debug(
            "embedding.index.document",
            extra={"source_id": document.source_id, "chunks": len(rows)},
        )
        return len(rows), deleted

    # -- Hjælpere -----------------------------------------------------------

    def _delete_chunks(self, document_id: int) -> int:
        result = self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        return int(result.rowcount or 0)

    def _delete_all_chunks(self) -> int:
        """Nulstiller hele indekset.

        Bemærk `expire_all()` til sidst. Sessionsfabrikken kører med
        ``expire_on_commit=False``, så ORM-objekter beholder deres værdier
        efter en commit. En massopdatering uden om ORM'en ville derfor
        efterlade objekterne med de gamle værdier, og næste tildeling af
        præcis samme værdi ville SQLAlchemy opfatte som "ingen ændring" og
        slet ikke skrive den — hvorved dokumentet blev stående som
        uvektoriseret i databasen, mens koden troede det var færdigt.
        """
        result = self.session.execute(delete(DocumentChunk))
        self.session.execute(
            update(Document).values(
                embedded_version_id=None,
                embedding_model=None,
                embedded_at=None,
                chunk_count=0,
            )
        )
        self.session.commit()
        self.session.expire_all()
        return int(result.rowcount or 0)

    def _write_pgvector(self, rows: list[DocumentChunk], vectors) -> None:
        """Spejler BLOB-vektorerne over i pgvector-kolonnen.

        BLOB'en er sandheden; denne kolonne er indekset. Skrives i samme
        transaktion, så de to aldrig kan komme ud af trit.
        """
        payload = [
            {"id": row.id, "vec": to_pgvector_literal(vector)}
            for row, vector in zip(rows, vectors, strict=True)
        ]
        if not payload:
            return
        self.session.execute(
            sql_text(
                "UPDATE document_chunks SET embedding_vec = CAST(:vec AS vector) WHERE id = :id"
            ),
            payload,
        )

    # -- Status -------------------------------------------------------------

    def coverage(self) -> dict:
        """Hvor stor en del af det maritime materiale er vektoriseret."""
        model = self.provider.info.model

        maritime_total = self.session.scalar(
            select(func.count()).select_from(Document).where(Document.is_maritime.is_(True))
        ) or 0
        embedded = self.session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.is_maritime.is_(True),
                Document.embedded_version_id.is_not(None),
                Document.embedded_version_id == Document.current_version_id,
                Document.embedding_model == model,
            )
        ) or 0
        chunks = self.session.scalar(select(func.count()).select_from(DocumentChunk)) or 0
        stale_model = self.session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.embedding_model != model)
        ) or 0

        return {
            "model": model,
            "provider": self.provider.info.provider,
            "semantic": self.provider.info.semantic,
            "dimensions": self.provider.info.dimensions,
            "pgvector": self._use_pgvector,
            "maritime_documents": maritime_total,
            "embedded_documents": embedded,
            "pending_documents": self.pending_count(),
            "chunks": chunks,
            "chunks_from_other_model": stale_model,
            "coverage_pct": round(100.0 * embedded / maritime_total, 1) if maritime_total else 0.0,
        }

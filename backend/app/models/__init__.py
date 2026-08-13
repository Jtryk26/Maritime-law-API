"""SQLAlchemy-modeller for den maritime lovdatabase.

Skemaoversigt::

    documents ──1:N──> document_versions
        │  └──current_version_id──> document_versions (aktuel version)
        ├──M:N──> categories   (via document_categories)
        └──1:N──> change_log

    import_runs ──1:N──> change_log
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow

__all__ = [
    "Document",
    "DocumentVersion",
    "Category",
    "DocumentCategory",
    "ImportRun",
    "ChangeLogEntry",
    "ChangeType",
    "ImportStatus",
]


class ChangeType(str, enum.Enum):
    """Typer af registrerede ændringer."""

    CREATED = "CREATED"
    CONTENT_UPDATED = "CONTENT_UPDATED"
    METADATA_UPDATED = "METADATA_UPDATED"
    STATUS_CHANGED = "STATUS_CHANGED"


class ImportStatus(str, enum.Enum):
    """Status for en importkørsel."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Dokumenter
# ---------------------------------------------------------------------------


class Document(Base):
    """Det logiske juridiske dokument, uafhængigt af dets versioner.

    Indholdet ligger i :class:`DocumentVersion`. Denne tabel bærer
    identitet, normaliseret metadata, maritim klassifikation og en
    peger til den aktuelle version.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- Herkomst -----------------------------------------------------------
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="retsinformation")
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    # Retsinformations accessionsnummer, f.eks. "B20220122005".
    retsinformation_id: Mapped[str | None] = mapped_column(String(128))
    # Lov-/bekendtgørelsesnummer som brugeren kender det, f.eks. "1290".
    # Søgbart selvstændigt felt, da praktikere ofte søger på nummer.
    document_number: Mapped[str | None] = mapped_column(String(64), index=True)
    # True når dokumentet stammer fra en fixturkilde. Systemet må ALDRIG
    # præsentere syntetiske data som var de hentet fra Retsinformation.
    is_synthetic: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)

    # --- Normaliseret metadata ---------------------------------------------
    title: Mapped[str] = mapped_column(Text, nullable=False)
    short_title: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str | None] = mapped_column(String(128), index=True)
    authority: Mapped[str | None] = mapped_column(String(256), index=True)
    published_date: Mapped[date | None] = mapped_column(Date, index=True)
    effective_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(64), index=True)

    # --- Maritim klassifikation --------------------------------------------
    is_maritime: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    maritime_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    # Matchede termer, negative signaler og begrundelse fra relevansmotoren.
    relevance_details: Mapped[dict | None] = mapped_column(JSON)
    # Navnet på den motor der foretog vurderingen ("keyword", senere "hybrid").
    relevance_engine: Mapped[str | None] = mapped_column(String(64))
    # Hvilken dokumentversion vurderingen blev beregnet på. Uden denne
    # kan man ikke efterprøve en klassifikation af en lovtekst der siden
    # er ændret.
    relevance_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    # --- Søgning ------------------------------------------------------------
    # Flade, portable søgefelter. Begge gemmes FOLDET (æ->ae, ø->oe,
    # å->aa, små bogstaver), så søgning er uafhængig af om brugeren
    # skriver "søulykke" eller "soeulykke".
    # PostgreSQL bruger derudover kolonnen `search_vector` (tsvector),
    # som tilføjes i migrationen og opdateres samme sted.
    search_text: Mapped[str | None] = mapped_column(Text)
    #: Kun titel, korttitel og dokumentnummer — bruges til titelrangering.
    search_title: Mapped[str | None] = mapped_column(Text)

    # --- Versionspeger ------------------------------------------------------
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    # --- Tidsstempler -------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    last_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Relationer ---------------------------------------------------------
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="DocumentVersion.document_id",
        order_by="DocumentVersion.version_number",
        passive_deletes=True,
    )
    current_version: Mapped["DocumentVersion | None"] = relationship(
        foreign_keys=[current_version_id],
        post_update=True,
        viewonly=True,
    )
    relevance_version: Mapped["DocumentVersion | None"] = relationship(
        foreign_keys=[relevance_version_id],
        post_update=True,
        viewonly=True,
    )
    category_links: Mapped[list["DocumentCategory"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    change_log: Mapped[list["ChangeLogEntry"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ChangeLogEntry.created_at.desc()",
        passive_deletes=True,
    )

    __table_args__ = (
        # Et kildedokument må kun findes én gang pr. kilde.
        UniqueConstraint("source", "source_id", name="uq_documents_source_source_id"),
        Index("ix_documents_retsinformation_id", "retsinformation_id"),
        Index("ix_documents_maritime_lookup", "is_maritime", "maritime_score"),
        Index("ix_documents_document_number", "document_number"),
        CheckConstraint(
            "maritime_score >= 0 AND maritime_score <= 100",
            name="maritime_score_range",
        ),
    )

    @property
    def categories(self) -> list["Category"]:
        return [link.category for link in self.category_links]

    def __repr__(self) -> str:  # pragma: no cover - kun til fejlsøgning
        return f"<Document id={self.id} source_id={self.source_id!r} score={self.maritime_score}>"


class DocumentVersion(Base):
    """Et uforanderligt øjebliksbillede af et dokuments indhold.

    Historiske versioner overskrives aldrig. Ved indholdsændring
    oprettes en ny række med næste versionsnummer.
    """

    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # SHA-256 over normaliseret indhold.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # SHA-256 over normaliseret metadata — adskiller METADATA_UPDATED fra CONTENT_UPDATED.
    metadata_hash: Mapped[str | None] = mapped_column(String(64))
    # Rå metadata fra kilden, bevaret uændret af hensyn til sporbarhed.
    metadata_json: Mapped[dict | None] = mapped_column(JSON)

    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    document: Mapped["Document"] = relationship(
        back_populates="versions",
        foreign_keys=[document_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_document_id_version"
        ),
        Index("ix_document_versions_document_version", "document_id", "version_number"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DocumentVersion doc={self.document_id} v={self.version_number}>"


# ---------------------------------------------------------------------------
# Kategorier
# ---------------------------------------------------------------------------


class Category(Base):
    """Maritim emnekategori. Seedes fra config/categories.yaml."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    document_links: Mapped[list["DocumentCategory"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Category {self.slug}>"


class DocumentCategory(Base):
    """Kobling mellem dokument og kategori med tildelingssikkerhed."""

    __tablename__ = "document_categories"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    # 0.0–1.0. Meningsfuld allerede med regelbaseret tildeling og
    # klar til senere AI-baseret kategorisering.
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    matched_terms: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    document: Mapped["Document"] = relationship(back_populates="category_links")
    category: Mapped["Category"] = relationship(back_populates="document_links")

    __table_args__ = (
        Index("ix_document_categories_category_id", "category_id"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_range"),
    )


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


class ImportRun(Base):
    """Registrering af en importkørsel."""

    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="retsinformation")
    # Hvilken klient der blev brugt: "fixture" eller "production".
    client_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="fixture")
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documents_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ImportStatus.RUNNING.value, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    # Pr-dokument fejldetaljer: [{"source_id": ..., "error": ...}, ...]
    errors: Mapped[list | None] = mapped_column(JSON)

    change_entries: Mapped[list["ChangeLogEntry"]] = relationship(back_populates="import_run")

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ImportRun id={self.id} status={self.status}>"


class ChangeLogEntry(Base):
    """Let ændringslog. Version 1 registrerer at noget ændrede sig,
    ikke en juridisk fortolkning af ændringen."""

    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_runs.id", ondelete="SET NULL"), index=True
    )
    old_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    new_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    change_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    document: Mapped["Document"] = relationship(back_populates="change_log")
    import_run: Mapped["ImportRun | None"] = relationship(back_populates="change_entries")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChangeLogEntry doc={self.document_id} type={self.change_type}>"

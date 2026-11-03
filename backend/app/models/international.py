"""Additive provenance and rights records for explicitly registered sources.

Legal text remains in DocumentVersion; no second document store is introduced.
Existing Danish documents are not backfilled or assigned inferred permissions.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class LegalSource(Base):
    __tablename__ = "legal_sources"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    official_hosts: Mapped[list] = mapped_column(JSON, nullable=False)
    terms_url: Mapped[str | None] = mapped_column(String(2000))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RightsEvidence(Base):
    __tablename__ = "legal_rights_evidence"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_key: Mapped[str] = mapped_column(ForeignKey("legal_sources.key"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    allowed_actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence_url: Mapped[str | None] = mapped_column(String(2000))
    reviewer: Mapped[str | None] = mapped_column(String(256))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attribution: Mapped[str | None] = mapped_column(Text)
    restrictions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class LegalProvenance(Base):
    __tablename__ = "legal_provenance"

    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), primary_key=True)
    provider_key: Mapped[str] = mapped_column(ForeignKey("legal_sources.key"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rights_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_version: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    retrieval_url: Mapped[str | None] = mapped_column(String(8000))
    source_hash_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="normalized_metadata")
    language: Mapped[str | None] = mapped_column(String(16))
    jurisdiction: Mapped[str | None] = mapped_column(String(64))
    adopted_date: Mapped[date | None] = mapped_column(Date)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    evidence_links: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    coverage_status: Mapped[str] = mapped_column(String(40), nullable=False, default="metadata_only")
    rights_evidence_id: Mapped[str | None] = mapped_column(ForeignKey("legal_rights_evidence.id"))


class InstrumentIdentifier(Base):
    __tablename__ = "legal_instrument_ids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    provider_key: Mapped[str] = mapped_column(ForeignKey("legal_sources.key"), nullable=False)
    scheme: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(256), nullable=False)

    __table_args__ = (UniqueConstraint("provider_key", "scheme", "value", name="uq_legal_instrument_identity"),)


class AcquisitionJob(Base):
    """Acquisition audit; scheduling remains an explicit CLI operation."""

    __tablename__ = "legal_acquisition_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_key: Mapped[str] = mapped_column(ForeignKey("legal_sources.key"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))

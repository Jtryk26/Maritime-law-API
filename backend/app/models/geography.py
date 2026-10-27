"""Geografisk anvendelighed: områder og dokumenters fund i dem.

Tre begreber holdes adskilt (se docs/GEOGRAPHIC_MAP_FEATURE.md):

* **Omtale** — teksten nævner et sted (`DocumentGeographicApplicability`
  med ``applicability = MENTIONS``).
* **Anvendelighed** — reglen gælder dér (``APPLIES``, ``EXEMPTS`` …),
  altid med confidence, metode og gennemgangsstatus. Automatisk
  klassifikation er aldrig en juridisk afgørelse.
* **Geometri** — kortets repræsentation af området
  (`GeographicArea.geometry`). ``geometry_kind`` siger, om det er et
  omtrentligt kortmærke eller en officiel grænse.

Dokumentets tekst ændres aldrig; fundene ligger i deres egen tabel og
peger på dokument, version og tegnposition.
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


class AreaType(str, enum.Enum):
    GLOBAL = "global"
    SEA_AREA = "sea_area"
    STRAIT = "strait"
    TERRITORIAL_WATERS = "territorial_waters"
    STATE = "state"
    MARPOL_SPECIAL_AREA = "marpol_special_area"
    ECA = "eca"
    PSSA = "pssa"
    ROUTEING_MEASURE = "routeing_measure"
    POLAR_WATERS = "polar_waters"


class GeometryKind(str, enum.Enum):
    #: Ingen geometri. Området findes kun som navn (fx "Globalt").
    NONE = "none"
    #: Et omtrentligt kortmærke. Aldrig en grænse.
    INDICATIVE_POINT = "indicative_point"
    #: En tilnærmet polygon fra en navngiven, ikke-officiel kilde.
    APPROXIMATE = "approximate"
    #: Grænse fra en officiel kilde (IMO, HELCOM, lovtekst). Kilden står i
    #: `geometry_source`.
    OFFICIAL = "official"


class Applicability(str, enum.Enum):
    APPLIES = "APPLIES"
    MENTIONS = "MENTIONS"
    EXEMPTS = "EXEMPTS"
    DEFINES = "DEFINES"
    HISTORICAL = "HISTORICAL"


class GeoReviewStatus(str, enum.Enum):
    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    #: Et menneske har rettet klassifikationen; rækken er nu manuel.
    CORRECTED = "corrected"
    #: Automatisk fund, hvor en tidligere gennemgang ikke længere passer med
    #: teksten. Behandles som ikke-gennemgået, indtil et menneske ser på det.
    NEEDS_REREVIEW = "needs_rereview"


#: Statusser, der tæller som menneskelig godkendelse. Alt andet er ikke-gennemgået.
APPROVED_REVIEW_STATUSES = (GeoReviewStatus.CONFIRMED.value, GeoReviewStatus.CORRECTED.value)


class EvidenceStatus(str, enum.Enum):
    #: Fundet hører til dokumentets nuværende tekst.
    CURRENT = "current"
    #: Teksten er ændret siden; rækken er historik og vises ikke som gældende.
    SUPERSEDED = "superseded"


def _in(enum_cls) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


class GeographicArea(Base):
    """Et kurateret geografisk område. Seedes fra config/geographic_areas.yaml."""

    __tablename__ = "geographic_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    name_da: Mapped[str | None] = mapped_column(String(256))
    area_type: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_slug: Mapped[str | None] = mapped_column(String(128))
    #: "descriptive" (et almindeligt geografisk navn), "curated_unverified"
    #: (juridisk område, metadata ikke efterprøvet) eller "verified".
    legal_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="curated_unverified"
    )
    legal_basis: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    source_reference: Mapped[str | None] = mapped_column(String(512))
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    aliases: Mapped[list | None] = mapped_column(JSON)

    #: GeoJSON-geometri (Point, Polygon, MultiPolygon, LineString …), WGS 84.
    geometry: Mapped[dict | None] = mapped_column(JSON)
    geometry_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    geometry_source: Mapped[str | None] = mapped_column(String(1024))
    #: Bounding box afledt af `geometry`. Portabel erstatning for et
    #: PostGIS-indeks: bbox-forespørgsler virker også på SQLite.
    min_lon: Mapped[float | None] = mapped_column(Float)
    min_lat: Mapped[float | None] = mapped_column(Float)
    max_lon: Mapped[float | None] = mapped_column(Float)
    max_lat: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("slug", name="uq_geographic_areas_slug"),
        Index("ix_geographic_areas_area_type", "area_type"),
        Index("ix_geographic_areas_bbox", "min_lon", "max_lon", "min_lat", "max_lat"),
        CheckConstraint(f"area_type IN ({_in(AreaType)})", name="type"),
        CheckConstraint(f"geometry_kind IN ({_in(GeometryKind)})", name="geometry_kind"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GeographicArea {self.slug}>"


class GeographicDetectionRun(Base):
    """Én analyse af ét dokument. `input_hash` afgør, om en ny er nødvendig."""

    __tablename__ = "geographic_detection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    detector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: SHA-256 over titel og indhold. Uændret hash + detektorversion = spring over.
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: "import", "admin", "cli".
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_geo_detection_runs_document", "document_id", "started_at"),
        CheckConstraint("status IN ('COMPLETED', 'FAILED', 'SKIPPED')", name="status"),
    )


class DocumentGeographicApplicability(Base):
    """Ét geografisk fund i ét dokument — med klassifikation og gennemgang."""

    __tablename__ = "document_geographic_applicability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    area_id: Mapped[int] = mapped_column(
        ForeignKey("geographic_areas.id", ondelete="CASCADE"), nullable=False
    )
    detection_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("geographic_detection_runs.id", ondelete="SET NULL")
    )
    #: "title" eller "content". Positionerne er relative til det felt.
    source_field: Mapped[str] = mapped_column(String(16), nullable=False)
    match_text: Mapped[str] = mapped_column(String(512), nullable=False)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    #: Dokumentsidens anker ("p-3-12-stk2"), når fundet ligger i en paragraf.
    paragraph_anchor: Mapped[str | None] = mapped_column(String(64))
    #: Sætningen omkring fundet, til gennemgang.
    context: Mapped[str | None] = mapped_column(Text)
    applicability: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    #: Den sproglige markør, der afgjorde klassifikationen ("gælder for").
    cue: Mapped[str | None] = mapped_column(String(128))
    #: "gazetteer", "gazetteer_fuzzy", "manual" (eller fremtidigt "llm").
    detection_method: Mapped[str] = mapped_column(String(32), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unreviewed")
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    #: SHA-256 over felt, fundet navn, ordret uddrag og dokumentstatus — det,
    #: en gennemgang tog stilling til. Se `services.geography.service.evidence_hash`.
    evidence_hash: Mapped[str | None] = mapped_column(String(64))
    #: Den juridiske kontekst, en godkendelse gælder for: version, titel,
    #: status og områdets definition. Se `services.geography.review_context_hash`.
    context_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_status: Mapped[str] = mapped_column(String(16), nullable=False, default="current")
    #: Det tidligere fund, dette afløser (en forældet eller videreført gennemgang).
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_geographic_applicability.id", ondelete="SET NULL", name="fk_doc_geo_supersedes_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    area: Mapped[GeographicArea] = relationship()

    __table_args__ = (
        Index("ix_doc_geo_document", "document_id"),
        Index("ix_doc_geo_area_applicability", "area_id", "applicability"),
        Index("ix_doc_geo_review_status", "review_status"),
        Index("ix_doc_geo_evidence_status", "evidence_status"),
        CheckConstraint(f"applicability IN ({_in(Applicability)})", name="applicability"),
        CheckConstraint(f"evidence_status IN ({_in(EvidenceStatus)})", name="evidence_status"),
        CheckConstraint(f"review_status IN ({_in(GeoReviewStatus)})", name="review_status"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
    )


class GeographicReviewQueueItem(Base):
    """Et stednavn uden område i gazetteeren — eller et andet tvivlstilfælde."""

    __tablename__ = "geographic_review_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    #: "unknown_area" (navnet findes ikke i gazetteeren) eller "fuzzy_match".
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Foldet navn — ét køpunkt pr. navn pr. dokument.
    candidate_key: Mapped[str] = mapped_column(String(255), nullable=False)
    match_text: Mapped[str] = mapped_column(String(512), nullable=False)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    context: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "document_id", "candidate_key", name="uq_geo_review_queue_document_candidate"
        ),
        Index("ix_geo_review_queue_status", "status"),
        CheckConstraint("status IN ('open', 'resolved', 'dismissed')", name="status"),
    )

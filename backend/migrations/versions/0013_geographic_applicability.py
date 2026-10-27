"""Geografisk anvendelighed: områder, fund, kørsler og gennemgangskø.

Tilføjer fire tabeller. Ingen eksisterende tabel ændres.

* ``geographic_areas`` — kuraterede områder (hav, stræde, søterritorium,
  MARPOL-særområde, ECA, PSSA, rutesystem). Geometrien gemmes som GeoJSON
  i en portabel JSON-kolonne med en afledt bounding box (``min_lon`` …
  ``max_lat``), så bbox-forespørgsler virker på både SQLite og PostgreSQL
  uden PostGIS. ``geometry_kind`` skelner et omtrentligt kortmærke
  (``indicative_point``) fra en officiel grænse (``official``).
* ``document_geographic_applicability`` — ét fund pr. række: hvilken tekst,
  hvor i dokumentet, hvilken klassifikation (APPLIES, MENTIONS, EXEMPTS,
  DEFINES, HISTORICAL), confidence, metode, detektorversion og
  menneskelig gennemgang. Dokumentets tekst røres ikke.
* ``geographic_detection_runs`` — én række pr. analyse af et dokument.
  ``input_hash`` gør det muligt at springe uændrede dokumenter over.
* ``geographic_review_queue`` — stednavne, der ikke findes i gazetteeren,
  og andre fund et menneske skal se på. Der opfindes aldrig en polygon.

``downgrade`` fjerner de fire tabeller. Fundene kan genskabes med
``python -m app.cli geography analyse``; gennemgangsstatus går tabt.

Revision ID: 0013_geographic_applicability
Revises: 0012_document_language_and_translation_rel
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_geographic_applicability"
down_revision = "0012_document_language_and_translation_rel"
branch_labels = None
depends_on = None

AREA_TYPES = (
    "'global', 'sea_area', 'strait', 'territorial_waters', 'state', "
    "'marpol_special_area', 'eca', 'pssa', 'routeing_measure', 'polar_waters'"
)
GEOMETRY_KINDS = "'none', 'indicative_point', 'approximate', 'official'"
APPLICABILITY = "'APPLIES', 'MENTIONS', 'EXEMPTS', 'DEFINES', 'HISTORICAL'"
REVIEW_STATUSES = "'unreviewed', 'confirmed', 'rejected', 'corrected'"


def upgrade() -> None:
    op.create_table(
        "geographic_areas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("name_da", sa.String(256), nullable=True),
        sa.Column("area_type", sa.String(32), nullable=False),
        sa.Column("parent_slug", sa.String(128), nullable=True),
        sa.Column("legal_status", sa.String(32), nullable=False, server_default="curated_unverified"),
        sa.Column("legal_basis", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(1024), nullable=True),
        sa.Column("source_reference", sa.String(512), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column("geometry", sa.JSON(), nullable=True),
        sa.Column("geometry_kind", sa.String(32), nullable=False, server_default="none"),
        sa.Column("geometry_source", sa.String(1024), nullable=True),
        sa.Column("min_lon", sa.Float(), nullable=True),
        sa.Column("min_lat", sa.Float(), nullable=True),
        sa.Column("max_lon", sa.Float(), nullable=True),
        sa.Column("max_lat", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_geographic_areas_slug"),
        sa.CheckConstraint(f"area_type IN ({AREA_TYPES})", name=op.f("ck_geographic_areas_type")),
        sa.CheckConstraint(
            f"geometry_kind IN ({GEOMETRY_KINDS})", name=op.f("ck_geographic_areas_geometry_kind")
        ),
    )
    op.create_index("ix_geographic_areas_area_type", "geographic_areas", ["area_type"])
    op.create_index(
        "ix_geographic_areas_bbox",
        "geographic_areas",
        ["min_lon", "max_lon", "min_lat", "max_lat"],
    )

    op.create_table(
        "geographic_detection_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("detector_version", sa.String(32), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('COMPLETED', 'FAILED', 'SKIPPED')",
            name=op.f("ck_geographic_detection_runs_status"),
        ),
    )
    op.create_index(
        "ix_geo_detection_runs_document",
        "geographic_detection_runs",
        ["document_id", "started_at"],
    )

    op.create_table(
        "document_geographic_applicability",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "area_id",
            sa.Integer(),
            sa.ForeignKey("geographic_areas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "detection_run_id",
            sa.Integer(),
            sa.ForeignKey("geographic_detection_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_field", sa.String(16), nullable=False),
        sa.Column("match_text", sa.String(512), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("paragraph_anchor", sa.String(64), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("applicability", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("cue", sa.String(128), nullable=True),
        sa.Column("detection_method", sa.String(32), nullable=False),
        sa.Column("detector_version", sa.String(32), nullable=False),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="unreviewed"),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"applicability IN ({APPLICABILITY})", name=op.f("ck_document_geographic_applicability_applicability")
        ),
        sa.CheckConstraint(
            f"review_status IN ({REVIEW_STATUSES})", name=op.f("ck_document_geographic_applicability_review_status")
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_document_geographic_applicability_confidence")
        ),
    )
    op.create_index(
        "ix_doc_geo_document", "document_geographic_applicability", ["document_id"]
    )
    op.create_index(
        "ix_doc_geo_area_applicability",
        "document_geographic_applicability",
        ["area_id", "applicability"],
    )
    op.create_index(
        "ix_doc_geo_review_status", "document_geographic_applicability", ["review_status"]
    )

    op.create_table(
        "geographic_review_queue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("candidate_key", sa.String(255), nullable=False),
        sa.Column("match_text", sa.String(512), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')", name=op.f("ck_geographic_review_queue_status")
        ),
        sa.UniqueConstraint(
            "document_id", "candidate_key", name="uq_geo_review_queue_document_candidate"
        ),
    )
    op.create_index("ix_geo_review_queue_status", "geographic_review_queue", ["status"])


def downgrade() -> None:
    op.drop_index("ix_geo_review_queue_status", table_name="geographic_review_queue")
    op.drop_table("geographic_review_queue")
    op.drop_index("ix_doc_geo_review_status", table_name="document_geographic_applicability")
    op.drop_index("ix_doc_geo_area_applicability", table_name="document_geographic_applicability")
    op.drop_index("ix_doc_geo_document", table_name="document_geographic_applicability")
    op.drop_table("document_geographic_applicability")
    op.drop_index("ix_geo_detection_runs_document", table_name="geographic_detection_runs")
    op.drop_table("geographic_detection_runs")
    op.drop_index("ix_geographic_areas_bbox", table_name="geographic_areas")
    op.drop_index("ix_geographic_areas_area_type", table_name="geographic_areas")
    op.drop_table("geographic_areas")

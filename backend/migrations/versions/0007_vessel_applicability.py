"""Vessel Applicability 2.0: flade regler med godkendelsesspor.

To tabeller:

``vessel_applicability_rules``
    Én regel pr. ``rule_id``. Betingelserne ligger som JSON og afgøres af
    `app.services.vessel_applicability.engine`. ``status`` er DRAFT,
    APPROVED eller DEPRECATED, og en APPROVED-række skal bære anmelder,
    signatur og den indholdshash, der blev godkendt.

``vessel_applicability_rule_events``
    Revisionsspor for udkast og godkendelser.

Migrationen er additiv og rører ingen eksisterende tabel.

Revision ID: 0007_vessel_applicability
Revises: 0006_applicability
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_vessel_applicability"
down_revision = "0006_applicability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vessel_applicability_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("legal_source_id", sa.String(length=128), nullable=False),
        sa.Column("citation", sa.String(length=500), nullable=False),
        sa.Column("topic_category", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("inclusion_conditions", sa.JSON(), nullable=False),
        sa.Column("exclusion_conditions", sa.JSON(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("keel_laid_after", sa.Date(), nullable=True),
        sa.Column("keel_laid_before", sa.Date(), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("source_text_hash", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("drafted_by", sa.String(length=128), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_signature", sa.Text(), nullable=True),
        sa.Column("approved_content_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_vessel_applicability_rules"),
        sa.UniqueConstraint("rule_id", name="uq_vessel_applicability_rules_rule_id"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'DEPRECATED')",
            name="ck_vessel_applicability_rules_status_known",
        ),
        sa.CheckConstraint(
            "status <> 'APPROVED' OR (approved_by IS NOT NULL "
            "AND approval_signature IS NOT NULL AND approved_content_hash IS NOT NULL)",
            name="ck_vessel_applicability_rules_approval_complete",
        ),
    )
    op.create_index(
        "ix_vessel_applicability_rules_legal_source_id",
        "vessel_applicability_rules",
        ["legal_source_id"],
    )
    op.create_index(
        "ix_vessel_applicability_rules_topic_category",
        "vessel_applicability_rules",
        ["topic_category"],
    )
    op.create_index(
        "ix_vessel_applicability_rules_status_topic",
        "vessel_applicability_rules",
        ["status", "topic_category"],
    )

    op.create_table(
        "vessel_applicability_rule_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_pk", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_status", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_vessel_applicability_rule_events"),
        sa.ForeignKeyConstraint(
            ["rule_pk"],
            ["vessel_applicability_rules.id"],
            name="fk_vessel_applicability_rule_events_rule_pk_vessel_applicability_rules",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_vessel_applicability_rule_events_rule_pk",
        "vessel_applicability_rule_events",
        ["rule_pk"],
    )


def downgrade() -> None:
    op.drop_table("vessel_applicability_rule_events")
    op.drop_table("vessel_applicability_rules")

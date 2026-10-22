"""Dokumentrelationer: historiske forgængere, ophævelser og hjemmel.

Tilføjer ``document_relations``. Én række pr. relation, gemt i den retning
kilden angiver den. ``target_document_id`` er NULL, indtil måldokumentet er
importeret; henvisningen står da i ``target_reference``.

Eksisterende dokumenter får relationer med
``python -m app.cli relations rebuild``. Nye og ændrede dokumenter får dem
ved import.

``downgrade`` fjerner tabellen. Relationerne kan genskabes fra lovteksten.

Revision ID: 0010_document_relations
Revises: 0009_vessel_rules_source_document
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_document_relations"
down_revision = "0009_vessel_rules_source_document"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_reference", sa.String(255), nullable=False),
        sa.Column("target_label", sa.String(512), nullable=True),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("specific_section", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "relation_type IN ('repeals', 'replaces', 'replaced_by', 'empowered_by', 'empowers')",
            name="ck_document_relations_type",
        ),
    )
    op.create_index(
        "ix_document_relations_source_type",
        "document_relations",
        ["source_document_id", "relation_type"],
    )
    op.create_index(
        "ix_document_relations_target_document_id", "document_relations", ["target_document_id"]
    )
    op.create_index(
        "ix_document_relations_target_reference", "document_relations", ["target_reference"]
    )


def downgrade() -> None:
    op.drop_index("ix_document_relations_target_reference", table_name="document_relations")
    op.drop_index("ix_document_relations_target_document_id", table_name="document_relations")
    op.drop_index("ix_document_relations_source_type", table_name="document_relations")
    op.drop_table("document_relations")

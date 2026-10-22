"""Vessel Applicability 2.0: regler bindes til den høstede kildeversion.

Tilføjer ``vessel_applicability_rules.document_version_id``. Fra og med denne
revision skal en regels ``source_text`` findes ordret (efter kanonisering) i en
høstet Retsinformation-version, før reglen kan udkastes eller godkendes, og
versionen indgår i reglens indholdshash.

Eksisterende regler får ``NULL`` — de er aldrig efterprøvet mod et
kildedokument og afgøres derfor ikke (``NEEDS_MANUAL_REVIEW``), før de er
udkastet igen via ``python -m app.cli vessel-rules ingest``. Ingen række
ændres; deres hashes er uberørte, fordi kildeversionen kun indgår i hashen,
når den er sat.

``downgrade`` fjerner kolonnen. Regler, der blev udkastet med en
kildeversion, kan derefter ikke efterprøves af ældre kode og vil vise sig som
kompromitterede, indtil de udkastes igen.

Revision ID: 0009_vessel_rules_source_document
Revises: 0008_vessel_rules_canonical_text_hash
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_vessel_rules_source_document"
down_revision = "0008_vessel_rules_canonical_text_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("vessel_applicability_rules") as batch:
        batch.add_column(sa.Column("document_version_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_var_doc_ver_id",
            "document_versions",
            ["document_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_vessel_applicability_rules_document_version_id", ["document_version_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("vessel_applicability_rules") as batch:
        batch.drop_index("ix_vessel_applicability_rules_document_version_id")
        batch.drop_constraint(
            "fk_var_doc_ver_id",
            type_="foreignkey",
        )
        batch.drop_column("document_version_id")

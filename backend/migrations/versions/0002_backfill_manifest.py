"""Efterindlæsningskø for historisk maritim lovgivning.

Retsinformations høsteservice rækker kun ti dage tilbage. Ældre
dokumenter kan derfor kun hentes ved at slå bestemte accessionsnumre op.
`backfill_manifest_items` er arbejdslisten over de numre, med
reservation (lease) og fencing token, så flere arbejdere kan dele køen
uden at overskrive hinandens tilstand.

Revision ID: 0002_backfill_manifest
Revises: 0001_initial
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_backfill_manifest"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backfill_manifest_items",
        sa.Column("accession_number", sa.String(length=64), nullable=False),
        sa.Column(
            "source_tag", sa.String(length=128), nullable=False, server_default="manual"
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="PENDING"
        ),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("import_run_id", sa.Integer(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'RETRY', 'FAILED', 'COMPLETED', 'REJECTED')",
            name="ck_backfill_manifest_items_status_valid",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_backfill_manifest_items_attempt_count_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["import_run_id"],
            ["import_runs.id"],
            name="fk_backfill_manifest_items_import_run_id_import_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("accession_number", name="pk_backfill_manifest_items"),
    )

    op.create_index(
        "ix_backfill_manifest_items_source_tag",
        "backfill_manifest_items",
        ["source_tag"],
    )
    op.create_index(
        "ix_backfill_manifest_items_status", "backfill_manifest_items", ["status"]
    )
    op.create_index(
        "ix_backfill_manifest_items_claim_token",
        "backfill_manifest_items",
        ["claim_token"],
    )
    op.create_index(
        "ix_backfill_manifest_items_next_attempt_at",
        "backfill_manifest_items",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_backfill_manifest_items_lease_expires_at",
        "backfill_manifest_items",
        ["lease_expires_at"],
    )
    # Sammensat indeks der matcher køopslagets ORDER BY.
    op.create_index(
        "ix_backfill_manifest_items_queue",
        "backfill_manifest_items",
        ["status", "priority", "next_attempt_at"],
    )


def downgrade() -> None:
    for name in (
        "ix_backfill_manifest_items_queue",
        "ix_backfill_manifest_items_lease_expires_at",
        "ix_backfill_manifest_items_next_attempt_at",
        "ix_backfill_manifest_items_claim_token",
        "ix_backfill_manifest_items_status",
        "ix_backfill_manifest_items_source_tag",
    ):
        op.drop_index(name, table_name="backfill_manifest_items")
    op.drop_table("backfill_manifest_items")

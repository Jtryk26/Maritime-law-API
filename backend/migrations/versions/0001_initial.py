"""Version 1 — indledende skema for den maritime lovdatabase.

Opretter dokumenter, versioner, kategorier, importkørsler og ændringslog.

PostgreSQL får derudover en vægtet `search_vector` (tsvector) med
GIN-indeks til fuldtekstsøgning. Kolonnen oprettes betinget, da SQLite
ikke har tsvector; dér bruges den portable `search_text`-kolonne, som
begge databaser deler.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # categories
    # ------------------------------------------------------------------
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.UniqueConstraint("slug", name="uq_categories_slug"),
    )
    op.create_index("ix_categories_slug", "categories", ["slug"], unique=True)

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("retsinformation_id", sa.String(length=128), nullable=True),
        sa.Column("document_number", sa.String(length=64), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("short_title", sa.Text(), nullable=True),
        sa.Column("document_type", sa.String(length=128), nullable=True),
        sa.Column("authority", sa.String(length=256), nullable=True),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("is_maritime", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("maritime_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relevance_details", sa.JSON(), nullable=True),
        sa.Column("relevance_engine", sa.String(length=64), nullable=True),
        sa.Column("relevance_version_id", sa.Integer(), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=True),
        sa.Column("search_title", sa.Text(), nullable=True),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("source", "source_id", name="uq_documents_source_source_id"),
        sa.CheckConstraint(
            "maritime_score >= 0 AND maritime_score <= 100",
            name="ck_documents_maritime_score_range",
        ),
    )
    op.create_index("ix_documents_document_type", "documents", ["document_type"])
    op.create_index("ix_documents_authority", "documents", ["authority"])
    op.create_index("ix_documents_published_date", "documents", ["published_date"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_is_maritime", "documents", ["is_maritime"])
    op.create_index("ix_documents_maritime_score", "documents", ["maritime_score"])
    op.create_index("ix_documents_is_synthetic", "documents", ["is_synthetic"])
    op.create_index("ix_documents_retsinformation_id", "documents", ["retsinformation_id"])
    op.create_index("ix_documents_document_number", "documents", ["document_number"])
    op.create_index(
        "ix_documents_maritime_lookup", "documents", ["is_maritime", "maritime_score"]
    )

    # ------------------------------------------------------------------
    # document_versions
    # ------------------------------------------------------------------
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata_hash", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_versions_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_document_id_version"
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_document_versions_version_number_positive"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_content_hash", "document_versions", ["content_hash"])
    op.create_index(
        "ix_document_versions_document_version",
        "document_versions",
        ["document_id", "version_number"],
    )

    # Cirkulære referencer: documents peger på sin aktuelle version, og
    # versionen peger tilbage på dokumentet. Nøglerne tilføjes derfor
    # efter begge tabeller findes.
    with op.batch_alter_table("documents") as batch:
        batch.create_foreign_key(
            "fk_documents_current_version_id_document_versions",
            "document_versions",
            ["current_version_id"],
            ["id"],
            ondelete="SET NULL",
            use_alter=True,
        )
        batch.create_foreign_key(
            "fk_documents_relevance_version_id_document_versions",
            "document_versions",
            ["relevance_version_id"],
            ["id"],
            ondelete="SET NULL",
            use_alter=True,
        )

    # ------------------------------------------------------------------
    # document_categories
    # ------------------------------------------------------------------
    op.create_table(
        "document_categories",
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("matched_terms", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("document_id", "category_id", name="pk_document_categories"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_categories_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name="fk_document_categories_category_id_categories",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="ck_document_categories_confidence_range"
        ),
    )
    op.create_index(
        "ix_document_categories_category_id", "document_categories", ["category_id"]
    )

    # ------------------------------------------------------------------
    # import_runs
    # ------------------------------------------------------------------
    op.create_table(
        "import_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("client_kind", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("documents_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("errors", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_import_runs"),
    )
    op.create_index("ix_import_runs_started_at", "import_runs", ["started_at"])
    op.create_index("ix_import_runs_status", "import_runs", ["status"])

    # ------------------------------------------------------------------
    # change_log
    # ------------------------------------------------------------------
    op.create_table(
        "change_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("import_run_id", sa.Integer(), nullable=True),
        sa.Column("old_version_id", sa.Integer(), nullable=True),
        sa.Column("new_version_id", sa.Integer(), nullable=True),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_change_log"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_change_log_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_run_id"],
            ["import_runs.id"],
            name="fk_change_log_import_run_id_import_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["old_version_id"],
            ["document_versions.id"],
            name="fk_change_log_old_version_id_document_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["new_version_id"],
            ["document_versions.id"],
            name="fk_change_log_new_version_id_document_versions",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_change_log_document_id", "change_log", ["document_id"])
    op.create_index("ix_change_log_import_run_id", "change_log", ["import_run_id"])
    op.create_index("ix_change_log_change_type", "change_log", ["change_type"])
    op.create_index("ix_change_log_created_at", "change_log", ["created_at"])

    # ------------------------------------------------------------------
    # PostgreSQL: fuldtekstsøgning
    # ------------------------------------------------------------------
    if _is_postgres():
        op.execute("ALTER TABLE documents ADD COLUMN search_vector tsvector")
        # GIN er det rette indeks til tsvector-opslag.
        op.execute(
            "CREATE INDEX ix_documents_search_vector ON documents USING GIN (search_vector)"
        )
        # Understøtter delvist match og 'ILIKE'-opslag på titler.
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX ix_documents_title_trgm ON documents USING GIN (title gin_trgm_ops)"
        )


def downgrade() -> None:
    if _is_postgres():
        op.execute("DROP INDEX IF EXISTS ix_documents_title_trgm")
        op.execute("DROP INDEX IF EXISTS ix_documents_search_vector")
        op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS search_vector")

    op.drop_table("change_log")
    op.drop_table("import_runs")
    op.drop_table("document_categories")

    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint(
            "fk_documents_relevance_version_id_document_versions", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_documents_current_version_id_document_versions", type_="foreignkey"
        )

    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("categories")

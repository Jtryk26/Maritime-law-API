"""Semantisk søgning: chunks, vektorer og søgelog.

Tre ting kommer til:

1. `document_chunks` — lovteksten delt i stykker, hvert med sin vektor.
2. `search_queries` — de søgninger der faktisk stilles, også vektoriseret.
3. Fire kolonner på `documents`, der fortæller hvilken version og hvilken
   model dokumentets vektorer stammer fra.

Vektorerne gemmes portabelt som float32-BLOB i alle databaser. På
PostgreSQL tilføjes derudover en pgvector-kolonne med et HNSW-indeks —
men **kun hvis udvidelsen faktisk er til stede**. Migrationen prøver
ikke bare `CREATE EXTENSION` og håber: en fejlslagen `CREATE EXTENSION`
afbryder hele transaktionen, og så ville opgraderingen vælte på en
database uden pgvector. Derfor spørges `pg_available_extensions` først.

Uden pgvector virker alt stadig — søgningen falder til den portable
brute force-sti, og `embed status` siger tydeligt hvad der mangler.

Dimensionen kommer fra EMBEDDING_DIMENSIONS på migrationstidspunktet.
Skiftes embedding-model til en med anden vektorlængde, skal kolonnen
genskabes: `python -m app.cli embed vector-column --recreate`.

Revision ID: 0004_semantic_search
Revises: 0003_curated_relevance_overrides
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_semantic_search"
down_revision = "0003_curated_relevance_overrides"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _pgvector_available() -> bool:
    """Er pgvector installeret på serveren?

    Spørger inden vi forsøger at oprette udvidelsen. Et fejlet
    `CREATE EXTENSION` ville rulle hele migrationstransaktionen tilbage.
    """
    result = op.get_bind().execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    ).first()
    return result is not None


def _dimensions() -> int:
    from app.core.config import get_settings

    return int(get_settings().embedding_dimensions)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # documents: hvilken version og model er vektoriseret
    # ------------------------------------------------------------------
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("embedded_version_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("embedding_model", sa.String(length=128), nullable=True))
        batch.add_column(
            sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0")
        )

    op.create_index("ix_documents_embedding_model", "documents", ["embedding_model"])

    # Nøglen tilføjes i batch-tilstand, ligesom de to øvrige
    # versionspegere i 0001: SQLite kan ikke ALTER TABLE ... ADD
    # CONSTRAINT, og batch-tilstand løser det ved at genskabe tabellen.
    # `use_alter` fordi documents og document_versions peger på hinanden.
    with op.batch_alter_table("documents") as batch:
        batch.create_foreign_key(
            "fk_documents_embedded_version_id_document_versions",
            "document_versions",
            ["embedded_version_id"],
            ["id"],
            ondelete="SET NULL",
            use_alter=True,
        )

    # ------------------------------------------------------------------
    # document_chunks
    # ------------------------------------------------------------------
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("heading", sa.String(length=256), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("char_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_id_index"
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            # Kort navn: NAMING_CONVENTION sætter selv ck_-præfikset på.
            name="chunk_index_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_versions.id"],
            name="fk_document_chunks_version_id_document_versions",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_version_id", "document_chunks", ["version_id"])
    op.create_index("ix_document_chunks_embedding_model", "document_chunks", ["embedding_model"])
    op.create_index(
        "ix_document_chunks_model_lookup", "document_chunks", ["embedding_model", "document_id"]
    )

    # ------------------------------------------------------------------
    # search_queries
    # ------------------------------------------------------------------
    op.create_table(
        "search_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("normalized_query", sa.Text(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("best_result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_mode", sa.String(length=16), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_search_queries"),
        sa.UniqueConstraint("query_hash", name="uq_search_queries_query_hash"),
        sa.CheckConstraint("occurrences >= 1", name="occurrences_positive"),
    )
    op.create_index("ix_search_queries_query_hash", "search_queries", ["query_hash"], unique=True)
    op.create_index("ix_search_queries_embedding_model", "search_queries", ["embedding_model"])
    op.create_index("ix_search_queries_occurrences", "search_queries", ["occurrences"])
    op.create_index("ix_search_queries_last_seen_at", "search_queries", ["last_seen_at"])
    op.create_index(
        "ix_search_queries_popularity", "search_queries", ["occurrences", "last_seen_at"]
    )

    # ------------------------------------------------------------------
    # PostgreSQL: pgvector
    # ------------------------------------------------------------------
    if _is_postgres() and _pgvector_available():
        dim = _dimensions()
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute(f"ALTER TABLE document_chunks ADD COLUMN embedding_vec vector({dim})")
        op.execute(f"ALTER TABLE search_queries ADD COLUMN embedding_vec vector({dim})")
        # HNSW med cosinus-afstand. Vektorerne er normaliserede, så
        # cosinus og prikprodukt rangerer ens; cosinus vælges fordi det
        # er den operator brugerne af tabellen forventer.
        op.execute(
            "CREATE INDEX ix_document_chunks_embedding_vec ON document_chunks "
            "USING hnsw (embedding_vec vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX ix_search_queries_embedding_vec ON search_queries "
            "USING hnsw (embedding_vec vector_cosine_ops)"
        )


def downgrade() -> None:
    if _is_postgres():
        op.execute("DROP INDEX IF EXISTS ix_search_queries_embedding_vec")
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_vec")
        op.execute("ALTER TABLE search_queries DROP COLUMN IF EXISTS embedding_vec")
        op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding_vec")

    op.drop_table("search_queries")
    op.drop_table("document_chunks")

    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint(
            "fk_documents_embedded_version_id_document_versions", type_="foreignkey"
        )
        batch.drop_index("ix_documents_embedding_model")
        batch.drop_column("chunk_count")
        batch.drop_column("embedded_at")
        batch.drop_column("embedding_model")
        batch.drop_column("embedded_version_id")

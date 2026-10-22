"""Exact source editions and transport provenance for rights-managed versions."""

from alembic import op
import sqlalchemy as sa

revision = "0012_eurlex_provenance"
down_revision = "0011_source_rights"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("legal_provenance", sa.Column("source_version", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("legal_provenance", sa.Column("rights_context", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("legal_provenance", sa.Column("retrieval_url", sa.String(8000)))
    op.add_column("legal_provenance", sa.Column("source_hash_scope", sa.String(64), nullable=False,
                                               server_default="normalized_metadata"))


def downgrade():
    with op.batch_alter_table("legal_provenance") as batch:
        batch.drop_column("source_hash_scope")
        batch.drop_column("retrieval_url")
        batch.drop_column("source_version")
        batch.drop_column("rights_context")

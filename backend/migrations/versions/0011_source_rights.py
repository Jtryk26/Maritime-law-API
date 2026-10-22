"""Add source, instrument identifiers, version provenance and rights evidence.

No legacy document rows are changed. Downgrade removes only these new tables;
remove managed documents/derived content through the rights service first.
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_source_rights"
down_revision = "0010_document_relations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "legal_sources",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("official_hosts", sa.JSON(), nullable=False),
        sa.Column("terms_url", sa.String(2000)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "legal_rights_evidence",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("provider_key", sa.String(64), sa.ForeignKey("legal_sources.key"), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("allowed_actions", sa.JSON(), nullable=False),
        sa.Column("evidence_url", sa.String(2000)),
        sa.Column("reviewer", sa.String(256)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("attribution", sa.Text()),
        sa.Column("restrictions", sa.JSON(), nullable=False),
    )
    op.create_table(
        "legal_provenance",
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("provider_key", sa.String(64), sa.ForeignKey("legal_sources.key"), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("language", sa.String(16)),
        sa.Column("jurisdiction", sa.String(64)),
        sa.Column("adopted_date", sa.Date()),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_links", sa.JSON(), nullable=False),
        sa.Column("coverage_status", sa.String(40), nullable=False),
        sa.Column("rights_evidence_id", sa.String(128), sa.ForeignKey("legal_rights_evidence.id")),
    )
    op.create_table(
        "legal_instrument_ids",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_key", sa.String(64), sa.ForeignKey("legal_sources.key"), nullable=False),
        sa.Column("scheme", sa.String(32), nullable=False),
        sa.Column("value", sa.String(256), nullable=False),
        sa.UniqueConstraint("provider_key", "scheme", "value", name="uq_legal_instrument_identity"),
    )
    op.create_table(
        "legal_acquisition_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_key", sa.String(64), sa.ForeignKey("legal_sources.key"), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
    )


def downgrade() -> None:
    # Do not expose previously gated text to code which predates the rights gate.
    bind = op.get_bind()
    if not op.get_context().as_sql and bind.execute(sa.text(
        "SELECT 1 FROM documents WHERE source IN (SELECT key FROM legal_sources) LIMIT 1"
    )).first():
        raise RuntimeError("Remove managed source documents before downgrading rights protection")
    for table in ("legal_acquisition_jobs", "legal_instrument_ids", "legal_provenance", "legal_rights_evidence", "legal_sources"):
        op.drop_table(table)

"""Geografiske fund: bevisets fingeraftryk og gennemgang, der er blevet forældet.

Ændringer på ``document_geographic_applicability``
================================================
* ``evidence_hash`` — SHA-256 over det, et menneske tog stilling til:
  feltet, det fundne navn, det ordrette tekstuddrag og dokumentets status.
* ``context_hash`` — SHA-256 over den juridiske kontekst, en godkendelse
  gælder for: dokumentversion, titel, retlig status og områdets definition.
  Ændres noget af det, gælder godkendelsen ikke længere, og den overføres
  aldrig automatisk til et nyt fund.
* ``evidence_status`` — ``current`` eller ``superseded``. En gennemgang af
  et fund, hvis bevis ikke længere findes i dokumentets tekst, bevares som
  historik (``superseded``) og anvendes aldrig på den ændrede tekst.
* ``supersedes_id`` — det tidligere fund (typisk en gennemgang), dette fund
  afløser.
* ``review_status`` tillader nu også ``needs_rereview``: et automatisk fund,
  hvor en tidligere gennemgang ikke længere passer med teksten.

Eksisterende rækker får ``evidence_status = 'current'`` og intet
``evidence_hash``; næste analyse sætter det.

Revision ID: 0014_geographic_review_evidence
Revises: 0013_geographic_applicability
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_geographic_review_evidence"
down_revision = "0013_geographic_applicability"
branch_labels = None
depends_on = None

_TABLE = "document_geographic_applicability"
_REVIEW_CK = "ck_document_geographic_applicability_review_status"
_EVIDENCE_CK = "ck_document_geographic_applicability_evidence_status"
_OLD_REVIEW = "'unreviewed', 'confirmed', 'rejected', 'corrected'"
_NEW_REVIEW = f"{_OLD_REVIEW}, 'needs_rereview'"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE, recreate="auto") as batch:
        batch.add_column(sa.Column("evidence_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_hash", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("evidence_status", sa.String(16), nullable=False, server_default="current")
        )
        batch.add_column(
            sa.Column(
                "supersedes_id",
                sa.Integer(),
                sa.ForeignKey(
                    f"{_TABLE}.id",
                    ondelete="SET NULL",
                    name="fk_doc_geo_supersedes_id",
                ),
                nullable=True,
            )
        )
        batch.drop_constraint(op.f(_REVIEW_CK), type_="check")
        batch.create_check_constraint(op.f(_REVIEW_CK), f"review_status IN ({_NEW_REVIEW})")
        batch.create_check_constraint(
            op.f(_EVIDENCE_CK), "evidence_status IN ('current', 'superseded')"
        )
        batch.create_index("ix_doc_geo_evidence_status", ["evidence_status"])


def downgrade() -> None:
    op.execute(f"UPDATE {_TABLE} SET review_status = 'unreviewed' WHERE review_status = 'needs_rereview'")
    with op.batch_alter_table(_TABLE, recreate="auto") as batch:
        batch.drop_index("ix_doc_geo_evidence_status")
        batch.drop_constraint(op.f(_EVIDENCE_CK), type_="check")
        batch.drop_constraint(op.f(_REVIEW_CK), type_="check")
        batch.create_check_constraint(op.f(_REVIEW_CK), f"review_status IN ({_OLD_REVIEW})")
        batch.drop_constraint("fk_doc_geo_supersedes_id", type_="foreignkey")
        batch.drop_column("supersedes_id")
        batch.drop_column("evidence_status")
        batch.drop_column("context_hash")
        batch.drop_column("evidence_hash")

"""Samler de to grene af migrationskæden igen.

#32 (EUR-Lex Cellar, ``0012_eurlex_provenance``) blev merget efter #33 og
#34, men bygger stadig direkte på ``0011_source_rights``. Kæden fik derfor to
heads: ``0012_eurlex_provenance`` og ``0014_geographic_review_evidence``, og
``alembic upgrade head`` fejlede.

Grenene rører ikke de samme tabeller (``legal_provenance`` mod ``documents``,
``document_relations`` og de geografiske tabeller), så rækkefølgen er
ligegyldig. Migrationen ændrer intet skema; den virker både på databaser, der
allerede har kørt ``0012_eurlex_provenance``, og på dem, der ikke har.

Revision ID: 0015_merge_eurlex_and_geography
Revises: 0012_eurlex_provenance, 0014_geographic_review_evidence
"""

from __future__ import annotations

revision = "0015_merge_eurlex_and_geography"
down_revision = ("0012_eurlex_provenance", "0014_geographic_review_evidence")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""Kurateret relevans-override — permanent, revisionssikker menneskelig afgørelse.

En global discovery-triage godkendte 539 accessionsnumre. Backfillen
importerede 522 og afviste 17, fordi den automatiske fuldtekstscore lå
under lagringstærsklen. Efter menneskelig kontrol viste 16 af de 17 sig
at være maritime og skal importeres; ét (køleanlæg/varmepumper) skal
forblive afvist.

To tabeller:

* `curated_relevance_overrides` — den AKTUELLE effektive afgørelse pr.
  accessionsnummer. Overskrives ved ændring.
* `curated_relevance_override_events` — append-only historik. Hver
  mutation (oprettelse, skift af beslutning, rettet begrundelse/tag,
  fjernelse) skriver en uforanderlig række med både forrige og ny
  tilstand, så et forløb kan rekonstrueres — også efter at overriden er
  slettet.

Ingen af dem rører den automatiske motors egen udregning:
`documents.maritime_score` og `documents.relevance_details` forbliver
altid det, motoren faktisk fandt.

Constraint- og indeksnavne herunder er identiske med dem, modellernes
metadata genererer via NAMING_CONVENTION i `app/db/base.py`. Se
`tests/test_curated_relevance_override.py::TestSkemaOverensstemmelse`,
som fejler, hvis de to nogensinde kommer ud af trit.

Revision ID: 0003_curated_relevance_overrides
Revises: 0002_backfill_manifest
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_curated_relevance_overrides"
down_revision = "0002_backfill_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "curated_relevance_overrides",
        sa.Column("accession_number", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column(
            "decision_source", sa.String(length=32), nullable=False, server_default="curated"
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_tag", sa.String(length=128), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('include', 'exclude')",
            # Kort navn: Alembic anvender selv NAMING_CONVENTION
            # ("ck_%(table_name)s_%(constraint_name)s"), så et
            # allerede prefikset navn ville blive fordoblet til
            # ck_curated_relevance_overrides_ck_curated_relevance_overrides_...
            name="decision_valid",
        ),
        sa.PrimaryKeyConstraint("accession_number", name="pk_curated_relevance_overrides"),
    )

    op.create_index(
        "ix_curated_relevance_overrides_decision",
        "curated_relevance_overrides",
        ["decision"],
    )
    op.create_index(
        "ix_curated_relevance_overrides_source_tag",
        "curated_relevance_overrides",
        ["source_tag"],
    )

    # Append-only historik. Bevidst UDEN fremmednøgle til tabellen ovenfor:
    # historikken skal overleve, at overriden fjernes.
    op.create_table(
        "curated_relevance_override_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("accession_number", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("previous_decision", sa.String(length=16), nullable=True),
        sa.Column("new_decision", sa.String(length=16), nullable=True),
        sa.Column("previous_reason", sa.Text(), nullable=True),
        sa.Column("new_reason", sa.Text(), nullable=True),
        sa.Column("previous_source_tag", sa.String(length=128), nullable=True),
        sa.Column("new_source_tag", sa.String(length=128), nullable=True),
        sa.Column("decision_source", sa.String(length=32), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('CREATED', 'DECISION_CHANGED', 'DETAILS_UPDATED', 'CLEARED')",
            # Kort navn — se kommentaren ovenfor.
            name="event_type_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_curated_relevance_override_events"),
    )

    op.create_index(
        "ix_curated_relevance_override_events_event_type",
        "curated_relevance_override_events",
        ["event_type"],
    )
    # Historikopslaget er altid "alle hændelser for ét accessionsnummer,
    # i tidsrækkefølge". Dækker også opslag på accession_number alene.
    op.create_index(
        "ix_curated_override_events_accession_created",
        "curated_relevance_override_events",
        ["accession_number", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_curated_override_events_accession_created",
        table_name="curated_relevance_override_events",
    )
    op.drop_index(
        "ix_curated_relevance_override_events_event_type",
        table_name="curated_relevance_override_events",
    )
    op.drop_table("curated_relevance_override_events")

    op.drop_index(
        "ix_curated_relevance_overrides_source_tag",
        table_name="curated_relevance_overrides",
    )
    op.drop_index(
        "ix_curated_relevance_overrides_decision",
        table_name="curated_relevance_overrides",
    )
    op.drop_table("curated_relevance_overrides")

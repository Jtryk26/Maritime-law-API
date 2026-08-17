"""Anvendelighed: regler, citater, betingelser og menneskelig gennemgang.

Syv tabeller, der tilsammen gør et anvendelsesområde til data uden at gøre et
regex-match til en juridisk konklusion:

``applicability_rules``
    Bestemmelsen og dens gyldighed, bundet til den **dokumentversion** teksten
    blev læst fra — samme binding som ``documents.relevance_version_id``. Uden
    den kan et citat ikke slås op i den tekst, det stammer fra.

``applicability_citations``
    Ordret skoptekst med position i kildeteksten.

``applicability_conditions``
    Betingelsestræet, normaliseret. En anmelder kan rette én grænse uden at
    redigere JSON i hånden.

``applicability_exclusions`` / ``applicability_discretion``
    Undtagelses- og skønsbestemmelser. Deres betingelser ligger i
    conditions-træet, mærket med ``clause_kind`` og ``clause_id``.

``applicability_coverage_gaps``
    Led i anvendelsesområdet, der ikke er omsat til betingelser. Så længe der
    står rækker her, kan reglen ikke give et rent ``APPLIES``.

``applicability_draft_runs`` / ``applicability_review_events``
    Drift og revisionsspor.

Migrationen er additiv: ingen eksisterende tabel røres, og ingen eksisterende
række ændres. Efter opgradering findes ingen regler — de skabes med::

    python -m app.cli applicability draft --scope maritime

og bliver først brugt af den offentlige vurdering, når et menneske har
godkendt dem.

Revision ID: 0006_applicability
Revises: 0005_structural_ranking
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_applicability"
down_revision = "0005_structural_ranking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # applicability_draft_runs
    # ------------------------------------------------------------------
    op.create_table(
        "applicability_draft_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RUNNING"),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="maritime"),
        sa.Column("documents_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_without_scope", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trigger", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_applicability_draft_runs"),
    )

    # ------------------------------------------------------------------
    # applicability_rules
    # ------------------------------------------------------------------
    op.create_table(
        "applicability_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=True),
        sa.Column("rule_ref", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("authority", sa.String(length=256), nullable=True),
        sa.Column("document_type", sa.String(length=64), nullable=True),
        sa.Column("flag_states", sa.JSON(), nullable=True),
        sa.Column("operating_areas", sa.JSON(), nullable=True),
        sa.Column("port_state_applies", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status_state", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("in_force_from", sa.Date(), nullable=True),
        sa.Column("in_force_to", sa.Date(), nullable=True),
        sa.Column("superseded_by_rule_id", sa.Integer(), nullable=True),
        sa.Column("status_citation_key", sa.String(length=64), nullable=True),
        sa.Column("jurisdiction_citation_key", sa.String(length=64), nullable=True),
        sa.Column("coverage_level", sa.String(length=16), nullable=False, server_default="unparsed"),
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="parser"),
        sa.Column("draft_run_id", sa.Integer(), nullable=True),
        sa.Column("bindingness", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("speciality_boost", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_applicability_rules"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_applicability_rules_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_applicability_rules_document_version_id_document_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_rule_id"],
            ["applicability_rules.id"],
            name="fk_applicability_rules_superseded_by_rule_id_applicability_rules",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["draft_run_id"],
            ["applicability_draft_runs.id"],
            name="fk_applicability_rules_draft_run_id_applicability_draft_runs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "document_id",
            "document_version_id",
            "rule_ref",
            name="uq_applicability_rules_document_id_version_ref",
        ),
        sa.CheckConstraint(
            "coverage_level IN ('complete', 'partial', 'unparsed')",
            name="ck_applicability_rules_coverage_level_known",
        ),
        sa.CheckConstraint(
            "review_status IN ('draft', 'approved', 'rejected', 'needs_changes')",
            name="ck_applicability_rules_review_status_known",
        ),
        sa.CheckConstraint(
            "bindingness BETWEEN 1 AND 4", name="ck_applicability_rules_bindingness_range"
        ),
    )
    op.create_index("ix_applicability_rules_document_id", "applicability_rules", ["document_id"])
    op.create_index(
        "ix_applicability_rules_document_version_id", "applicability_rules", ["document_version_id"]
    )
    op.create_index("ix_applicability_rules_draft_run_id", "applicability_rules", ["draft_run_id"])
    # Sammensatte indeks med den mest selektive kolonne først. Ingen
    # enkeltkolonne-indeks oveni: førstekolonnen dækker de opslag også.
    op.create_index(
        "ix_applicability_rules_review_lookup",
        "applicability_rules",
        ["review_status", "document_id"],
    )
    op.create_index(
        "ix_applicability_rules_status_lookup",
        "applicability_rules",
        ["status_state", "in_force_from"],
    )

    # ------------------------------------------------------------------
    # applicability_citations
    # ------------------------------------------------------------------
    op.create_table(
        "applicability_citations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("citation_key", sa.String(length=64), nullable=False),
        sa.Column("ref", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="inclusion"),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("document_version_id", sa.Integer(), nullable=True),
        sa.Column("text_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_applicability_citations"),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["applicability_rules.id"],
            name="fk_applicability_citations_rule_id_applicability_rules",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_applicability_citations_document_version_id_document_versions",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "rule_id", "citation_key", name="uq_applicability_citations_rule_id_key"
        ),
    )
    op.create_index("ix_applicability_citations_rule_id", "applicability_citations", ["rule_id"])

    # ------------------------------------------------------------------
    # applicability_conditions
    # ------------------------------------------------------------------
    op.create_table(
        "applicability_conditions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("clause_kind", sa.String(length=16), nullable=False, server_default="inclusion"),
        sa.Column("clause_id", sa.String(length=64), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("node_type", sa.String(length=8), nullable=False, server_default="atom"),
        sa.Column("field_name", sa.String(length=64), nullable=True),
        sa.Column("op", sa.String(length=16), nullable=True),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("citation_key", sa.String(length=64), nullable=True),
        sa.Column("strength", sa.String(length=16), nullable=False, server_default="hard"),
        sa.Column("tolerance", sa.Float(), nullable=True),
        sa.Column("unknown_policy", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("always_value", sa.Boolean(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("draft_confidence", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_applicability_conditions"),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["applicability_rules.id"],
            name="fk_applicability_conditions_rule_id_applicability_rules",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["applicability_conditions.id"],
            name="fk_applicability_conditions_parent_id_applicability_conditions",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "node_type IN ('all', 'any', 'not', 'atom', 'always')",
            name="ck_applicability_conditions_node_type_known",
        ),
        sa.CheckConstraint(
            "clause_kind IN ('inclusion', 'exclusion', 'discretion')",
            name="ck_applicability_conditions_clause_kind_known",
        ),
    )
    op.create_index("ix_applicability_conditions_rule_id", "applicability_conditions", ["rule_id"])
    op.create_index("ix_applicability_conditions_parent_id", "applicability_conditions", ["parent_id"])
    op.create_index("ix_applicability_conditions_clause_id", "applicability_conditions", ["clause_id"])
    op.create_index(
        "ix_applicability_conditions_rule_clause",
        "applicability_conditions",
        ["rule_id", "clause_kind", "clause_id"],
    )

    # ------------------------------------------------------------------
    # applicability_exclusions
    # ------------------------------------------------------------------
    op.create_table(
        "applicability_exclusions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("clause_id", sa.String(length=64), nullable=False),
        sa.Column("citation_key", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_applicability_exclusions"),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["applicability_rules.id"],
            name="fk_applicability_exclusions_rule_id_applicability_rules",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "rule_id", "clause_id", name="uq_applicability_exclusions_rule_id_clause"
        ),
    )
    op.create_index("ix_applicability_exclusions_rule_id", "applicability_exclusions", ["rule_id"])

    # ------------------------------------------------------------------
    # applicability_discretion
    # ------------------------------------------------------------------
    op.create_table(
        "applicability_discretion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("clause_id", sa.String(length=64), nullable=False),
        sa.Column(
            "authority", sa.String(length=256), nullable=False, server_default="Søfartsstyrelsen"
        ),
        sa.Column("effect", sa.String(length=16), nullable=False, server_default="may_exempt"),
        sa.Column("citation_key", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_applicability_discretion"),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["applicability_rules.id"],
            name="fk_applicability_discretion_rule_id_applicability_rules",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "rule_id", "clause_id", name="uq_applicability_discretion_rule_id_clause"
        ),
        sa.CheckConstraint(
            "effect IN ('may_extend', 'may_exempt', 'may_modify')",
            name="ck_applicability_discretion_effect_known",
        ),
    )
    op.create_index("ix_applicability_discretion_rule_id", "applicability_discretion", ["rule_id"])

    # ------------------------------------------------------------------
    # applicability_coverage_gaps
    # ------------------------------------------------------------------
    op.create_table(
        "applicability_coverage_gaps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("citation_key", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_applicability_coverage_gaps"),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["applicability_rules.id"],
            name="fk_applicability_coverage_gaps_rule_id_applicability_rules",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_applicability_coverage_gaps_rule_id", "applicability_coverage_gaps", ["rule_id"]
    )

    # ------------------------------------------------------------------
    # applicability_review_events
    # ------------------------------------------------------------------
    op.create_table(
        "applicability_review_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("previous_status", sa.String(length=16), nullable=True),
        sa.Column("previous_coverage_level", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_applicability_review_events"),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["applicability_rules.id"],
            name="fk_applicability_review_events_rule_id_applicability_rules",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_applicability_review_events_rule_id", "applicability_review_events", ["rule_id"]
    )
    op.create_index(
        "ix_applicability_review_events_created_at", "applicability_review_events", ["created_at"]
    )


def downgrade() -> None:
    op.drop_table("applicability_review_events")
    op.drop_table("applicability_coverage_gaps")
    op.drop_table("applicability_discretion")
    op.drop_table("applicability_exclusions")
    op.drop_table("applicability_conditions")
    op.drop_table("applicability_citations")
    op.drop_table("applicability_rules")
    op.drop_table("applicability_draft_runs")

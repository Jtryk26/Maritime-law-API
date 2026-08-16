"""Strukturel parsing, visningstitler og domænejusteret rangering.

To skemaændringer, som hører sammen:

1. ``documents`` får ``display_title``, ``law_class``, ``scope_score``,
   ``authority_score`` og ``niche_groups``. Titlen er delt i to — den
   juridisk korrekte i ``title`` og den korte i ``display_title`` — og de
   tre rangeringsfelter gør det muligt at prioritere brede kernelove over
   smalle særregler uden at genberegne noget ved hver søgning.

2. ``document_chunks`` får en juridisk adresse: ``unit_type``,
   ``chapter_no``, ``chapter_title``, ``section_no``, ``section_title``,
   ``paragraph_id``, ``paragraph_sort_key`` og ``full_citation``. Et
   stykke er nu så vidt muligt én paragraf, og det er den enhed
   retrieval'et returnerer.

Eksisterende rækker
===================
Kolonnerne er nullable eller har en server-default, så migrationen kan
køre på en fyldt database. Værdierne fyldes bagefter med::

    python -m app.cli ranking reclassify
    python -m app.cli embed run --reset

Første kommando kræver ingen model og er hurtig; anden bygger det
semantiske indeks om, fordi stykkegrænserne er ændret.

Revision ID: 0005_structural_ranking
Revises: 0004_semantic_search
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_structural_ranking"
down_revision = "0004_semantic_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # documents: titler og rangeringssignaler
    # ------------------------------------------------------------------
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("display_title", sa.Text(), nullable=True))
        batch.add_column(sa.Column("law_class", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("scope_score", sa.Float(), nullable=False, server_default="0.55")
        )
        batch.add_column(
            sa.Column("authority_score", sa.Float(), nullable=False, server_default="0.5")
        )
        batch.add_column(sa.Column("niche_groups", sa.JSON(), nullable=True))

    op.create_index("ix_documents_law_class", "documents", ["law_class"])
    # Forsidens "vigtige maritime love" og enhver bred søgning slår op på
    # netop denne kombination.
    op.create_index(
        "ix_documents_ranking_lookup",
        "documents",
        ["is_maritime", "law_class", "maritime_score"],
    )

    # Eksisterende rækker får en brugbar visningstitel med det samme, så
    # brugerfladen ikke skal falde tilbage til den lange titel i tiden
    # mellem migration og genklassificering.
    op.execute("UPDATE documents SET display_title = title WHERE display_title IS NULL")

    # ------------------------------------------------------------------
    # document_chunks: juridisk adresse
    # ------------------------------------------------------------------
    with op.batch_alter_table("document_chunks") as batch:
        batch.add_column(
            sa.Column(
                "unit_type",
                sa.String(length=16),
                nullable=False,
                server_default="paragraph",
            )
        )
        batch.add_column(sa.Column("chapter_no", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("chapter_title", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("section_no", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("section_title", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("paragraph_id", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("paragraph_sort_key", sa.String(length=16), nullable=True)
        )
        batch.add_column(sa.Column("full_citation", sa.Text(), nullable=True))

    op.create_index("ix_document_chunks_paragraph_id", "document_chunks", ["paragraph_id"])


def downgrade() -> None:
    op.drop_index("ix_document_chunks_paragraph_id", table_name="document_chunks")
    with op.batch_alter_table("document_chunks") as batch:
        batch.drop_column("full_citation")
        batch.drop_column("paragraph_sort_key")
        batch.drop_column("paragraph_id")
        batch.drop_column("section_title")
        batch.drop_column("section_no")
        batch.drop_column("chapter_title")
        batch.drop_column("chapter_no")
        batch.drop_column("unit_type")

    op.drop_index("ix_documents_ranking_lookup", table_name="documents")
    op.drop_index("ix_documents_law_class", table_name="documents")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("niche_groups")
        batch.drop_column("authority_score")
        batch.drop_column("scope_score")
        batch.drop_column("law_class")
        batch.drop_column("display_title")

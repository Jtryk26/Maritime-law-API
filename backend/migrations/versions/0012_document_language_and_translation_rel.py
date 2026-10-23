"""Dokumentsprog og oversættelsesrelationer.

Ændringer
=========
* ``documents.language`` — tekstens sprog (ISO 639-1), NOT NULL, standard
  ``'da'``. Eksisterende dokumenter er danske og får ``'da'``.
* ``document_relations.relation_type`` tillader nu også ``translation_of``
  (dansk bekendtgørelse -> den autentiske engelske traktattekst) og
  ``treaty_basis`` (forskrift -> den traktat, den gennemfører).
* Constrainten får samtidig sit rigtige navn, ``ck_document_relations_type``.
  0010 gav den ved en fejl navnet
  ``ck_document_relations_ck_document_relations_type``. Begge navne fjernes
  defensivt (``IF EXISTS``), så migrationen ikke fejler, hvis et miljø har
  det ene, det andet eller ingen af dem.

To-spaltede traktater (BKI) gemmes som to dokumenter — dansk og engelsk —
bundet sammen med ``translation_of``. Se ``ImportService._store_translation``.

Revision ID: 0012_document_language_and_translation_rel
Revises: 0011_source_rights
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_document_language_and_translation_rel"
down_revision = "0011_source_rights"
branch_labels = None
depends_on = None

_NAME = "ck_document_relations_type"
#: Navnet 0010 faktisk gav constrainten (se _replace_type_constraint).
_LEGACY_NAME = "ck_document_relations_ck_document_relations_type"
#: Begge navne, constrainten kan have i et miljø (se _replace_type_constraint).
_CANDIDATE_NAMES = (_LEGACY_NAME, _NAME)
_OLD_TYPES = "'repeals', 'replaces', 'replaced_by', 'empowered_by', 'empowers'"
_NEW_TYPES = f"{_OLD_TYPES}, 'translation_of', 'treaty_basis'"


def _replace_type_constraint(expression: str, *, name: str) -> None:
    """Erstatter check-constrainten på relation_type — defensivt.

    0010 gav ved en fejl constrainten navnet
    "ck_document_relations_ck_document_relations_type" (navnekonventionen
    "ck_%(table_name)s_%(constraint_name)s" blev lagt oven på et allerede
    fuldt navn), mens modellen forventer "ck_document_relations_type".
    Hvilket navn et miljø faktisk har, kan variere, så BEGGE fjernes, hvis
    de findes, før den nye tilføjes. En deployment må ikke fejle på et
    navn, der ikke findes.

    PostgreSQL: DROP CONSTRAINT IF EXISTS. SQLite kan ikke ændre en
    constraint på stedet; tabellen genskabes i batch-tilstand, og kun de
    navne, der faktisk findes, fjernes. op.f() markerer navnene som
    endelige, så navnekonventionen ikke anvendes igen.
    """
    if op.get_context().dialect.name == "postgresql":
        for candidate in _CANDIDATE_NAMES:
            op.execute(f"ALTER TABLE document_relations DROP CONSTRAINT IF EXISTS {candidate}")
        op.create_check_constraint(op.f(name), "document_relations", expression)
        return

    existing = {
        c["name"]
        for c in sa.inspect(op.get_bind()).get_check_constraints("document_relations")
    }
    with op.batch_alter_table("document_relations", recreate="always") as batch:
        for candidate in _CANDIDATE_NAMES:
            if candidate in existing:
                batch.drop_constraint(op.f(candidate), type_="check")
        batch.create_check_constraint(op.f(name), expression)


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(
            sa.Column("language", sa.String(10), nullable=False, server_default="da")
        )
        batch.create_index("ix_documents_language", ["language"])

    _replace_type_constraint(f"relation_type IN ({_NEW_TYPES})", name=_NAME)


def downgrade() -> None:
    op.execute(
        "DELETE FROM document_relations "
        "WHERE relation_type IN ('translation_of', 'treaty_basis')"
    )
    _replace_type_constraint(f"relation_type IN ({_OLD_TYPES})", name=_LEGACY_NAME)
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_language")
        batch.drop_column("language")

"""Migrationerne skal kunne køre på PostgreSQL — ikke kun på SQLite.

Testpakken kører mod SQLite, som hverken kender PostgreSQL's grænse på 63 tegn
for identifikatorer (``NAMEDATALEN``) eller dens dialekt. Migration 0007 og
0009 havde constraint-navne på 70 og 67 tegn; de bestod alle tests og fejlede
først i drift, hvor SQLAlchemy afviser dem før de sendes til databasen.

To kontroller uden en PostgreSQL-server:

1. **Statisk:** hvert constraint- og indeksnavn i hver migrationsfil.
2. **Kompilering:** migrationerne fra 0005 og frem genereres som
   PostgreSQL-SQL (Alembics offline-tilstand), hvor SQLAlchemy validerer
   identifikatorer og dialekt. Tidligere revisioner (0004) spørger databasen
   om pgvector og kan ikke køres offline; de er i drift i forvejen.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pytest
from alembic import command

from app.db.migrations_runner import BACKEND_DIR, alembic_config

PG_MAX_IDENTIFIER = 63
PG_URL = "postgresql+psycopg://u:p@localhost/offline"
MIGRATIONS = sorted((BACKEND_DIR / "migrations" / "versions").glob("*.py"))

_NAME = re.compile(r"""["']((?:fk|ix|uq|ck|pk)_[a-z0-9_]+)["']""")


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_ingen_navne_over_63_tegn_i_migrationen(path: Path):
    names = set(_NAME.findall(path.read_text(encoding="utf-8")))
    too_long = sorted(f"{n} ({len(n)})" for n in names if len(n) > PG_MAX_IDENTIFIER)
    assert not too_long, too_long


@pytest.fixture(scope="module")
def offline_sql() -> str:
    """0006→head som PostgreSQL-SQL. ``env.py`` læser URL'en fra indstillingerne."""
    from app.core.config import get_settings

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = PG_URL
    get_settings.cache_clear()
    try:
        config = alembic_config()
        buffer = io.StringIO()
        config.output_buffer = buffer
        command.upgrade(config, "0005_structural_ranking:head", sql=True)
        return buffer.getvalue()
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


def test_anvendelighedsmigrationerne_kompilerer_til_postgresql(offline_sql):
    assert "CREATE TABLE vessel_applicability_rules" in offline_sql
    assert "CONSTRAINT fk_vare_rule_pk_var FOREIGN KEY" in offline_sql
    assert "ADD CONSTRAINT fk_var_doc_ver_id FOREIGN KEY" in offline_sql
    assert "CREATE TABLE document_relations" in offline_sql
    assert "version_num='0010_document_relations'" in offline_sql
    # Kladderevisionen (unummereret, se migrationens docstring).
    assert "ALTER TABLE documents ADD COLUMN language VARCHAR(10) DEFAULT 'da' NOT NULL" in offline_sql
    # Defensivt: begge navne, constrainten kan have, fjernes kun hvis de findes.
    for name in ("ck_document_relations_ck_document_relations_type", "ck_document_relations_type"):
        assert f"ALTER TABLE document_relations DROP CONSTRAINT IF EXISTS {name};" in offline_sql
    assert "ADD CONSTRAINT ck_document_relations_type CHECK" in offline_sql
    assert "'translation_of', 'treaty_basis'" in offline_sql
    assert "version_num='document_language_and_translation_rel'" in offline_sql


def test_ingen_identifikator_i_den_genererede_sql_er_for_lang(offline_sql):
    names = set(re.findall(r"\b(?:CONSTRAINT|INDEX)\s+([a-z_][a-z0-9_]*)", offline_sql))
    assert names
    assert not {n for n in names if len(n) > PG_MAX_IDENTIFIER}


@pytest.mark.parametrize(
    "name", ["fk_vare_rule_pk_var", "fk_var_doc_ver_id", "ck_document_relations_type"]
)
def test_modellerne_bruger_samme_navne_som_migrationerne(name):
    import app.models  # noqa: F401 — registrerer tabellerne
    from app.db.base import Base

    model_names = {
        str(c.name) for t in Base.metadata.sorted_tables for c in t.constraints if c.name
    }
    assert name in model_names


ALEMBIC_DEFAULT_VERSION_LENGTH = 32


def test_alembic_version_udvides_foer_lange_revisionsid_skrives(offline_sql):
    """Alembic opretter version_num som VARCHAR(32). Et længere revisions-id må
    først skrives, efter kolonnen er udvidet — ellers fejler opgraderingen i
    PostgreSQL med 'value too long for type character varying(32)'."""
    widen = offline_sql.find(
        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)"
    )
    writes = [
        (m.start(), m.group(1))
        for m in re.finditer(r"UPDATE alembic_version SET version_num='([^']+)'", offline_sql)
    ]
    long_writes = [(pos, rev) for pos, rev in writes if len(rev) > ALEMBIC_DEFAULT_VERSION_LENGTH]
    assert long_writes, "forventede mindst ét langt revisions-id i kæden"
    assert widen != -1
    assert all(pos > widen for pos, _ in long_writes), long_writes

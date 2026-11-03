"""Opt-in live PostgreSQL checks in newly created throwaway databases only.

EURLEX_TEST_POSTGRES_ADMIN_URL must identify a local isolated test server with
CREATE DATABASE privilege. Never point it at shared/production infrastructure.
"""
import os
import uuid

import psycopg
import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.migrations_runner import alembic_config
from app.db.seed import seed_categories
from app.models import Document
from app.models.international import LegalProvenance
from app.services.eurlex.ingest import import_eurlex
from eurlex_support import cellar_transport, review_file

ADMIN_URL = os.environ.get("EURLEX_TEST_POSTGRES_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="isolated PostgreSQL admin URL not supplied")


@pytest.fixture()
def pg_database(monkeypatch):
    admin = make_url(ADMIN_URL)
    if admin.host not in {"127.0.0.1", "localhost"}:
        pytest.fail("These tests require an explicitly local isolated PostgreSQL server")
    name = "eurlex_m3_" + uuid.uuid4().hex
    connection = psycopg.connect(admin.set(drivername="postgresql").render_as_string(hide_password=False), autocommit=True)
    connection.execute(psycopg.sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(psycopg.sql.Identifier(name)))
    url = admin.set(database=name, drivername="postgresql+psycopg").render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("INTERNATIONAL_CATALOGUE_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "64")
    monkeypatch.setenv("RUN_MIGRATIONS_ON_STARTUP", "false")
    get_settings.cache_clear()
    engine = create_engine(url)
    try:
        yield engine
    finally:
        from app.db.session import reset_engine
        reset_engine()
        engine.dispose()
        connection.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(name)))
        connection.close()
        get_settings.cache_clear()


def test_postgresql_fresh_migrations_ingestion_rights_and_public_api(pg_database, tmp_path):
    command.upgrade(alembic_config(), "head")
    with pg_database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0012_eurlex_provenance"
        assert connection.scalar(text("SELECT extname FROM pg_extension WHERE extname='vector'")) == "vector"
    with Session(pg_database) as session:
        seed_categories(session)
        first = import_eurlex(session, enable_provider=True, rights_review=review_file(tmp_path), transport=cellar_transport())
        session.commit()
        assert first.created == 3 and first.failed == 0
        second = import_eurlex(session, transport=cellar_transport())
        session.commit()
        assert second.unchanged == 3 and not second.failed
        doc = session.scalar(select(Document).where(Document.source_id == "32009L0016"))
        assert doc.current_version.content
        provenance = session.get(LegalProvenance, doc.current_version_id)
        assert provenance.rights_evidence_id and provenance.source_version["celex"] == "32009L0016"
        assert session.scalar(text("SELECT count(*) FROM documents WHERE search_vector IS NOT NULL")) >= 1
        instrument_id = doc.id
    from app.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app()) as client:
        item = client.get(f"/api/instruments/{instrument_id}")
        assert item.status_code == 200 and item.json()["has_full_text"]
        body = client.get(f"/api/instruments/{instrument_id}/text")
        assert body.status_code == 200 and "Synthetic shipping" in body.json()["content"]
        assert client.get(f"/api/documents/{instrument_id}").status_code == 404


def test_postgresql_upgrade_from_merged_head_preserves_existing_provenance(pg_database):
    config = alembic_config()
    command.upgrade(config, "0011_source_rights")
    # Exercise the real predecessor schema without using newer ORM columns.
    with pg_database.begin() as connection:
        connection.execute(text("INSERT INTO legal_sources(key,name,official_hosts,enabled) VALUES ('fixture_source','Fixture','[]',false)"))
        doc_id = connection.scalar(text("INSERT INTO documents(source,source_id,title,created_at,updated_at) VALUES ('fixture_source','fixture','Old fixture',now(),now()) RETURNING id"))
        version_id = connection.scalar(text("INSERT INTO document_versions(document_id,version_number,content,content_hash,created_at) VALUES (:doc,1,'',:hash,now()) RETURNING id"), {"doc": doc_id, "hash": "a" * 64})
        connection.execute(text("INSERT INTO legal_provenance(version_id,provider_key,external_id,source_url,source_hash,retrieved_at,evidence_links,coverage_status) VALUES (:version,'fixture_source','fixture','https://example.eu/fixture',:hash,now(),'[]','metadata_only')"), {"version": version_id, "hash": "a" * 64})
    command.upgrade(config, "head")
    with pg_database.connect() as connection:
        assert connection.scalar(text("SELECT name FROM legal_sources WHERE key='fixture_source'")) == "Fixture"
        old = connection.execute(text("SELECT source_hash,source_hash_scope,source_version,rights_evidence_id FROM legal_provenance WHERE version_id=:version"), {"version": version_id}).one()
        assert old == ("a" * 64, "normalized_metadata", {}, None)
        columns = {row["name"] for row in inspect(connection).get_columns("legal_provenance")}
        assert {"source_version", "source_hash_scope", "retrieval_url"} <= columns
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0012_eurlex_provenance"
    command.downgrade(config, "0011_source_rights")
    command.upgrade(config, "head")
    with pg_database.connect() as connection:
        assert connection.scalar(text("SELECT name FROM legal_sources WHERE key='fixture_source'")) == "Fixture"

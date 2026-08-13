"""Fælles testopsætning.

Hver test kører mod en frisk SQLite-database, oprettet med de rigtige
Alembic-migrationer — ikke med create_all. Dermed testes det skema, der
faktisk udrulles.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def database_url(tmp_path: Path) -> Iterator[str]:
    """Isoleret database pr. test."""
    url = f"sqlite:///{tmp_path / 'test.db'}"

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    os.environ["RUN_MIGRATIONS_ON_STARTUP"] = "false"

    # Settings caches; ryd så den nye URL bruges.
    from app.core.config import get_settings
    from app.db.session import reset_engine

    get_settings.cache_clear()
    reset_engine()

    from app.db.migrations_runner import run_migrations

    run_migrations()

    yield url

    reset_engine()
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    get_settings.cache_clear()


@pytest.fixture()
def session(database_url: str) -> Iterator:
    from app.db.session import get_session_factory

    db = get_session_factory()()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture()
def seeded_session(session) -> Iterator:
    """Session med den maritime taksonomi indlæst."""
    from app.db.seed import seed_categories

    seed_categories(session)
    yield session


@pytest.fixture()
def relevance_engine():
    from app.services.relevance import KeywordRelevanceEngine

    return KeywordRelevanceEngine()


@pytest.fixture()
def categorization_engine():
    from app.services.categorization import KeywordCategorizationEngine

    return KeywordCategorizationEngine()


@pytest.fixture()
def fixture_client():
    from app.services.retsinformation import FixtureRetsinformationClient

    return FixtureRetsinformationClient(revision=1)


def make_document(
    title: str,
    content: str = "",
    *,
    source_id: str = "TEST-001",
    authority: str | None = None,
    status: str | None = "Gældende",
    document_type: str | None = "Bekendtgørelse",
    document_number: str | None = None,
):
    """Hjælper til at bygge et normaliseret testdokument."""
    from app.services.retsinformation.base import NormalizedDocument

    return NormalizedDocument(
        source="test",
        source_id=source_id,
        title=title,
        content=content,
        authority=authority,
        status=status,
        document_type=document_type,
        document_number=document_number,
    )


@pytest.fixture()
def api_client(database_url: str) -> Iterator:
    """FastAPI-testklient bundet til testdatabasen."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client

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
    previous_provider = os.environ.get("EMBEDDING_PROVIDER")
    previous_dimensions = os.environ.get("EMBEDDING_DIMENSIONS")
    os.environ["DATABASE_URL"] = url
    os.environ["RUN_MIGRATIONS_ON_STARTUP"] = "false"
    # Testpakken må ALDRIG hente en embedding-model ned. Hash-udbyderen
    # er deterministisk og kræver hverken netværk eller torch, så alt
    # omkring vektorerne — chunking, lagring, sammensmeltning, søgelog —
    # kan afprøves reproducerbart. At den ikke er semantisk er netop
    # derfor markeret i `ProviderInfo.semantic`, og de tests der handler
    # om selve modelkvaliteten findes ikke; de ville måle hash-støj.
    # Overskriv ubetinget: Docker Compose sætter provider=local i
    # containerens miljø. setdefault() ville derfor lade API'et bruge E5,
    # mens embedding_provider-fixturen skrev hashing-v1-vektorer — to
    # forskellige indeksmodeller i samme testdatabase.
    os.environ["EMBEDDING_PROVIDER"] = "hashing"
    os.environ["EMBEDDING_DIMENSIONS"] = "64"

    # Settings caches; ryd så den nye URL bruges.
    from app.core.config import get_settings
    from app.db.session import reset_engine
    from app.db.vector_support import reset_vector_support_cache
    from app.services.embedding import reset_embedding_provider

    get_settings.cache_clear()
    reset_engine()
    reset_embedding_provider()
    reset_vector_support_cache()

    from app.db.migrations_runner import run_migrations

    run_migrations()

    yield url

    reset_engine()
    reset_embedding_provider()
    reset_vector_support_cache()
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    if previous_provider is None:
        os.environ.pop("EMBEDDING_PROVIDER", None)
    else:
        os.environ["EMBEDDING_PROVIDER"] = previous_provider
    if previous_dimensions is None:
        os.environ.pop("EMBEDDING_DIMENSIONS", None)
    else:
        os.environ["EMBEDDING_DIMENSIONS"] = previous_dimensions
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


@pytest.fixture()
def embedding_provider():
    """Deterministisk udbyder. Se bemærkningen i `database_url`."""
    from app.services.embedding import HashingEmbeddingProvider

    return HashingEmbeddingProvider(dimensions=64)


@pytest.fixture()
def indexed_session(seeded_session, embedding_provider):
    """Session med fixturdokumenter importeret OG vektoriseret."""
    from app.services.categorization import KeywordCategorizationEngine
    from app.services.embedding import EmbeddingIndexer
    from app.services.importer import ImportService
    from app.services.relevance import KeywordRelevanceEngine
    from app.services.retsinformation import FixtureRetsinformationClient

    ImportService(
        seeded_session,
        client=FixtureRetsinformationClient(revision=1),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()

    EmbeddingIndexer(seeded_session, embedding_provider).index_pending()
    return seeded_session

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

#: Administratortokenet i testene. Sættes på modulniveau, fordi
#: `app.main` læser konfigurationen ved import, og importen sker første
#: gang en test beder om `api_client`.
TEST_ADMIN_TOKEN = "test-administratortoken-mindst-24-tegn"
os.environ["ADMIN_API_TOKEN"] = TEST_ADMIN_TOKEN

#: Rate limiting slås fra i testene. Middlewaren har tilstand pr. proces,
#: og `app.main.app` er ét objekt, der genbruges af alle tests — kvoten
#: ville altså blive delt mellem tests og gøre suiten afhængig af sin egen
#: rækkefølge. Selve begrænsningen afprøves i test_security.py, som bygger
#: sin egen applikation med sine egne grænser.
os.environ["RATE_LIMIT_ENABLED"] = "false"


def _fixture_source_ids(revision: int = 1) -> set[str]:
    """Kilde-id'erne i et fixtursæt, læst fra filen selv."""
    import json

    path = REPO_ROOT / "data" / "fixtures" / (
        "documents.json" if revision == 1 else f"documents_rev{revision}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {doc["source_id"] for doc in payload["documents"]}


#: Antallet af fixturdokumenter udledes af filerne frem for at stå som tal
#: i hver enkelt test. Ellers ville enhver tilføjelse af testmateriale —
#: og de nichedokumenter, rangeringen skal afprøves mod, ER testmateriale —
#: kræve at et halvt dusin urelaterede tests blev rettet.
FIXTURE_TOTAL = len(_fixture_source_ids(1))
#: De dokumenter der bevidst IKKE er maritime: folkeskole, dagtilbud,
#: luftfart. De skal afvises af relevansmotoren, og antallet er en reel
#: påstand om fixtursættet — derfor står det som et tal.
FIXTURE_NON_MARITIME = 3
FIXTURE_STORED = FIXTURE_TOTAL - FIXTURE_NON_MARITIME
#: Revision 2 er revision 1 med tre dokumenter ændret eller tilføjet.
FIXTURE_TOTAL_REV2 = len(_fixture_source_ids(1) | _fixture_source_ids(2))


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
    """FastAPI-testklient med administratoradgang.

    De fleste tests handler om funktionalitet, ikke om adgangskontrol, og
    skal kunne køre en import uden at forholde sig til tokens. Selve
    adgangskontrollen afprøves i test_security.py — dér bruges
    `public_api_client`, som ingen legitimation har.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, headers={"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}) as client:
        yield client


@pytest.fixture()
def public_api_client(database_url: str) -> Iterator:
    """Testklient uden legitimation — en almindelig besøgende."""
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

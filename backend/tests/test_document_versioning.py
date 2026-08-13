"""Test af versionering og indholdshashing.

Kravet er utvetydigt: historiske versioner må aldrig overskrives, og
uændret indhold må aldrig give en overflødig version.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.text import content_hash
from app.models import ChangeType, Document, DocumentVersion
from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import DocumentRepository
from app.services.relevance import KeywordRelevanceEngine
from tests.conftest import make_document


@pytest.fixture()
def repo(seeded_session):
    return DocumentRepository(seeded_session)


@pytest.fixture()
def engines():
    return KeywordRelevanceEngine(), KeywordCategorizationEngine()


def _store(repo, engines, document):
    relevance_engine, categorization_engine = engines
    return repo.store(
        document,
        relevance_engine.classify(document),
        categorization_engine.categorize(document),
    )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def test_hash_er_deterministisk():
    assert content_hash("§ 1. Skibet skal være sødygtigt.") == content_hash(
        "§ 1. Skibet skal være sødygtigt."
    )


def test_hash_ignorerer_ren_omformatering():
    """Kildens whitespace-ændringer er ikke en indholdsændring."""
    assert content_hash("§ 1.  Skibet\n\n skal være sødygtigt.") == content_hash(
        "§ 1. Skibet skal være sødygtigt."
    )


def test_hash_reagerer_paa_reel_tekstaendring():
    assert content_hash("§ 1. Skibet skal være sødygtigt.") != content_hash(
        "§ 1. Skibet skal være sødygtigt og bemandet."
    )


# ---------------------------------------------------------------------------
# Versionsforløb
# ---------------------------------------------------------------------------


def test_nyt_dokument_giver_version_1(repo, engines, seeded_session):
    doc = make_document("Bekendtgørelse om skibssikkerhed", "§ 1. Oprindelig tekst.")
    outcome = _store(repo, engines, doc)
    seeded_session.commit()

    assert outcome.created is True
    assert outcome.version_number == 1
    assert outcome.change_types == [ChangeType.CREATED]
    assert outcome.document.current_version_id is not None


def test_samme_indhold_giver_ingen_ny_version(repo, engines, seeded_session):
    doc = make_document("Bekendtgørelse om skibssikkerhed", "§ 1. Oprindelig tekst.")
    _store(repo, engines, doc)
    seeded_session.commit()

    outcome = _store(repo, engines, make_document(
        "Bekendtgørelse om skibssikkerhed", "§ 1. Oprindelig tekst."
    ))
    seeded_session.commit()

    assert outcome.created is False
    assert outcome.content_changed is False
    assert outcome.unchanged is True
    versions = seeded_session.scalars(select(DocumentVersion)).all()
    assert len(versions) == 1


def test_aendret_indhold_giver_version_2_og_bevarer_version_1(repo, engines, seeded_session):
    _store(repo, engines, make_document(
        "Bekendtgørelse om skibssikkerhed", "§ 1. Oprindelig tekst."
    ))
    seeded_session.commit()

    outcome = _store(repo, engines, make_document(
        "Bekendtgørelse om skibssikkerhed", "§ 1. Oprindelig tekst.\n§ 2. Ny bestemmelse."
    ))
    seeded_session.commit()

    assert outcome.content_changed is True
    assert outcome.version_number == 2
    assert ChangeType.CONTENT_UPDATED in outcome.change_types

    document = seeded_session.scalars(select(Document)).one()
    versions = sorted(document.versions, key=lambda v: v.version_number)
    assert [v.version_number for v in versions] == [1, 2]
    # Version 1 er urørt.
    assert versions[0].content == "§ 1. Oprindelig tekst."
    assert "Ny bestemmelse" in versions[1].content
    # Peger på den nye version.
    assert document.current_version_id == versions[1].id


def test_versionsnumre_stiger_konsistent(repo, engines, seeded_session):
    for i in range(1, 5):
        _store(repo, engines, make_document(
            "Bekendtgørelse om skibssikkerhed", f"§ 1. Tekst revision {i}."
        ))
        seeded_session.commit()

    document = seeded_session.scalars(select(Document)).one()
    numre = sorted(v.version_number for v in document.versions)
    assert numre == [1, 2, 3, 4]


def test_statusaendring_logges_uden_ny_version(repo, engines, seeded_session):
    _store(repo, engines, make_document(
        "Bekendtgørelse om lodspligt", "§ 1. Tekst.", status="Gældende"
    ))
    seeded_session.commit()

    outcome = _store(repo, engines, make_document(
        "Bekendtgørelse om lodspligt", "§ 1. Tekst.", status="Ophævet"
    ))
    seeded_session.commit()

    assert ChangeType.STATUS_CHANGED in outcome.change_types
    assert outcome.content_changed is False
    assert len(seeded_session.scalars(select(DocumentVersion)).all()) == 1
    assert seeded_session.scalars(select(Document)).one().status == "Ophævet"


def test_metadataaendring_giver_metadata_updated(repo, engines, seeded_session):
    _store(repo, engines, make_document(
        "Bekendtgørelse om skibssikkerhed", "§ 1. Tekst.", document_number="100"
    ))
    seeded_session.commit()

    outcome = _store(repo, engines, make_document(
        "Bekendtgørelse om skibssikkerhed", "§ 1. Tekst.", document_number="101"
    ))
    seeded_session.commit()

    assert ChangeType.METADATA_UPDATED in outcome.change_types
    assert len(seeded_session.scalars(select(DocumentVersion)).all()) == 1


def test_relevansvurdering_bindes_til_den_version_den_gaelder(repo, engines, seeded_session):
    """Uden denne binding kan en klassifikation ikke efterprøves."""
    _store(repo, engines, make_document("Bekendtgørelse om skibssikkerhed", "§ 1. Tekst."))
    seeded_session.commit()
    document = seeded_session.scalars(select(Document)).one()
    assert document.relevance_version_id == document.current_version_id

    _store(repo, engines, make_document(
        "Bekendtgørelse om skibssikkerhed", "§ 1. Tekst.\n§ 2. Mere tekst om besætning."
    ))
    seeded_session.commit()
    seeded_session.refresh(document)
    assert document.relevance_version_id == document.current_version_id


def test_version_gemmer_baade_normaliserede_og_raa_metadata(repo, engines, seeded_session):
    _store(repo, engines, make_document(
        "Bekendtgørelse om skibssikkerhed", "§ 1. Tekst.", authority="Søfartsstyrelsen"
    ))
    seeded_session.commit()

    version = seeded_session.scalars(select(DocumentVersion)).one()
    assert version.metadata_json is not None
    assert "normalized" in version.metadata_json
    assert version.metadata_json["normalized"]["authority"] == "Søfartsstyrelsen"
    assert "source" in version.metadata_json

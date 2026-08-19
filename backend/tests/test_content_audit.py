"""Skelnen mellem "vi mangler teksten" og "teksten findes ikke".

Diagnostikken talte 2.438 maritime dokumenter uden paragraftegn og
konkluderede foreløbigt, at parseren tabte tekst. Kilden viste noget
andet: for en stor del af dokumenterne leverer Retsinformation kun
``<Meta>``. De to tilfælde kræver hver sin handling, og testene her
holder skelnen på plads.
"""

from __future__ import annotations

import pytest

from app.models import Document
from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import DocumentRepository
from app.services.importer.content_audit import (
    UNSET_LABEL,
    reclassify,
    summarize_content_kinds,
)
from app.services.legal.content_kind import (
    CONTENT_KIND_FULL_TEXT,
    CONTENT_KIND_METADATA_ONLY,
    CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN,
)
from tests.conftest import make_document

PARAGRAF_TEKST = (
    "§ 1. Bekendtgørelsen gælder for passagerskibe.\n"
    "§ 2. Skibsføreren har ansvaret for brandberedskabet om bord."
)

UDEN_PARAGRAF = (
    "Cirkulæret orienterer om Søfartsstyrelsens praksis for syn af "
    "redningsmidler i handelsskibe og træder i kraft straks."
)


@pytest.fixture()
def repo(seeded_session):
    return DocumentRepository(seeded_session)


@pytest.fixture()
def engines():
    from app.services.relevance import KeywordRelevanceEngine

    return KeywordRelevanceEngine(), KeywordCategorizationEngine()


def _store(repo, engines, document):
    relevance_engine, categorization_engine = engines
    return repo.store(
        document,
        relevance_engine.classify(document),
        categorization_engine.categorize(document),
    )


def _document(session, source_id: str) -> Document:
    return session.query(Document).filter(Document.source_id == source_id).one()


# ---------------------------------------------------------------------------
# Import sætter indholdstypen
# ---------------------------------------------------------------------------


def test_fuldtekst_gemmes_som_full_text(repo, engines, seeded_session):
    doc = make_document(
        "Bekendtgørelse om brandsikkerhed i passagerskibe",
        PARAGRAF_TEKST,
        source_id="B-FULL",
        authority="Søfartsstyrelsen",
    )
    _store(repo, engines, doc)
    assert _document(seeded_session, "B-FULL").content_kind == CONTENT_KIND_FULL_TEXT


def test_kildens_egen_besked_vejer_tungest(repo, engines, seeded_session):
    """Kilden har ingen brødtekst. Det er ikke det samme som tom tekst
    af ukendt årsag, og forskellen må overleve helt ind i databasen."""
    doc = make_document(
        "Bekendtgørelse angaaende sejladsen gennem Limfjorden",
        "",
        source_id="B-META",
        authority="Søfartsstyrelsen",
    )
    doc.content_kind = CONTENT_KIND_METADATA_ONLY
    _store(repo, engines, doc)
    assert _document(seeded_session, "B-META").content_kind == CONTENT_KIND_METADATA_ONLY


def test_tekst_uden_paragraftegn_faar_egen_type(repo, engines, seeded_session):
    doc = make_document(
        "Cirkulære om syn af redningsmidler",
        UDEN_PARAGRAF,
        source_id="C-UDEN",
        authority="Søfartsstyrelsen",
        document_type="Cirkulære",
    )
    _store(repo, engines, doc)
    assert (
        _document(seeded_session, "C-UDEN").content_kind
        == CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN
    )


# ---------------------------------------------------------------------------
# Genberegning af eksisterende rækker
# ---------------------------------------------------------------------------


def test_reclassify_er_idempotent(repo, engines, seeded_session):
    _store(repo, engines, make_document("Lov om sikkerhed til søs", PARAGRAF_TEKST, source_id="A-1"))
    seeded_session.flush()

    report = reclassify(seeded_session)
    assert report.examined >= 1
    assert report.changed == 0


def test_reclassify_retter_manglende_vurdering(repo, engines, seeded_session):
    _store(repo, engines, make_document("Lov om sikkerhed til søs", PARAGRAF_TEKST, source_id="A-2"))
    seeded_session.flush()

    # Simulér en række fra før kolonnen fandtes.
    document = _document(seeded_session, "A-2")
    document.content_kind = None
    seeded_session.flush()

    report = reclassify(seeded_session)
    assert report.changed == 1
    assert report.transitions[(UNSET_LABEL, CONTENT_KIND_FULL_TEXT)] == 1
    seeded_session.expire_all()
    assert _document(seeded_session, "A-2").content_kind == CONTENT_KIND_FULL_TEXT


def test_reclassify_overskriver_ikke_kildens_metadata_only(repo, engines, seeded_session):
    """Uden herkomstflaget ville et metadata-only dokument blive
    omklassificeret til 'empty' ved hver genberegning."""
    doc = make_document("Bekendtgørelse uden fuldtekst", "", source_id="B-META-2")
    doc.content_kind = CONTENT_KIND_METADATA_ONLY
    _store(repo, engines, doc)
    seeded_session.flush()

    report = reclassify(seeded_session)
    assert report.changed == 0
    assert (
        _document(seeded_session, "B-META-2").content_kind == CONTENT_KIND_METADATA_ONLY
    )


def test_dry_run_skriver_ikke(repo, engines, seeded_session):
    _store(repo, engines, make_document("Lov om sikkerhed til søs", PARAGRAF_TEKST, source_id="A-3"))
    seeded_session.flush()
    document = _document(seeded_session, "A-3")
    document.content_kind = None
    seeded_session.flush()

    report = reclassify(seeded_session, dry_run=True)
    assert report.changed == 1
    seeded_session.expire_all()
    assert _document(seeded_session, "A-3").content_kind is None


# ---------------------------------------------------------------------------
# Opgørelsen
# ---------------------------------------------------------------------------


def test_summary_taeller_pr_type(repo, engines, seeded_session):
    _store(repo, engines, make_document("Lov om sikkerhed til søs", PARAGRAF_TEKST, source_id="S-1"))
    _store(
        repo,
        engines,
        make_document("Cirkulære om skibssyn", UDEN_PARAGRAF, source_id="S-2",
                      authority="Søfartsstyrelsen", document_type="Cirkulære"),
    )
    seeded_session.flush()

    summary = summarize_content_kinds(seeded_session)
    assert summary.total == 2
    assert summary.counts[CONTENT_KIND_FULL_TEXT] == 1
    assert summary.counts[CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN] == 1


def test_summary_udpeger_rakker_uden_herkomst(repo, engines, seeded_session):
    """Rækker importeret før flaget fandtes kan ikke afgøres offline.
    De skal tælles for sig, ikke gættes på plads."""
    _store(repo, engines, make_document("Lov om sikkerhed til søs", PARAGRAF_TEKST, source_id="S-3"))
    seeded_session.flush()

    document = _document(seeded_session, "S-3")
    version = document.current_version
    metadata = dict(version.metadata_json or {})
    metadata["normalized"] = {
        k: v for k, v in (metadata.get("normalized") or {}).items() if k != "content_kind"
    }
    metadata["source"] = {}
    version.metadata_json = metadata
    seeded_session.flush()

    summary = summarize_content_kinds(seeded_session)
    assert summary.unverified == 1


# ---------------------------------------------------------------------------
# API'et skal kunne fortælle brugeren hvorfor der ingen tekst er
# ---------------------------------------------------------------------------


@pytest.fixture()
def populated_api(api_client):
    response = api_client.post(
        "/api/import/run", json={"source_client": "fixture", "fixture_revision": 1}
    )
    assert response.status_code == 201
    return api_client


def test_stats_viser_tekstdaekning(populated_api):
    body = populated_api.get("/api/stats").json()
    fordeling = body["documents_by_content_kind"]
    assert fordeling, "tekstdækningen mangler i nøgletallene"
    assert sum(fordeling.values()) == body["documents_total"]


def test_dokumentsvar_baerer_content_kind(populated_api):
    body = populated_api.get("/api/documents", params={"page_size": 1}).json()
    assert body["items"], "ingen dokumenter importeret"
    assert body["items"][0]["content_kind"] in {
        CONTENT_KIND_FULL_TEXT,
        CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN,
        CONTENT_KIND_METADATA_ONLY,
        "empty",
    }

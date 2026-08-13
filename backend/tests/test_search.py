"""Test af søgning og filtrering."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Document
from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient
from app.services.search import SearchQuery, get_search_backend


@pytest.fixture()
def populated(seeded_session):
    ImportService(
        seeded_session,
        client=FixtureRetsinformationClient(revision=1),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()
    return seeded_session


def _search(session, **kwargs):
    return get_search_backend(session).search(session, SearchQuery(**kwargs))


def _titler(results) -> list[str]:
    return [hit.document.title for hit in results.hits]


# ---------------------------------------------------------------------------
# Fritekstsøgning
# ---------------------------------------------------------------------------


def test_soegning_paa_brand_passagerskib(populated):
    """Specifikationens eksempelsøgning skal finde det rigtige dokument."""
    results = _search(populated, q="brand passagerskib")
    assert results.total >= 1
    assert any("brandsikkerhed i passagerskibe" in t.lower() for t in _titler(results))


@pytest.mark.parametrize(
    "term,forventet_fragment",
    [
        ("brand", "brandsikkerhed"),
        ("passagerskib", "passagerskibe"),
        ("SOLAS", "brandsikkerhed"),
        ("MARPOL", "forurening"),
        ("redningsmidler", "redningsmidler"),
        ("Søfartsstyrelsen", ""),
        ("STCW", "uddannelse"),
        ("GMDSS", "radioudstyr"),
        ("søulykke", "søulykker"),
        ("lodspligt", "lodspligt"),
    ],
)
def test_maritim_terminologi_finder_dokumenter(populated, term, forventet_fragment):
    results = _search(populated, q=term)
    assert results.total >= 1, f"ingen træf for {term!r}"
    if forventet_fragment:
        assert any(forventet_fragment in t.lower() for t in _titler(results))


def test_soegning_uden_traef_giver_tomt_resultat(populated):
    results = _search(populated, q="kvantemekanik")
    assert results.total == 0
    assert results.hits == []


def test_titelmatch_rangerer_hoejest(populated):
    """Et dokument med søgeordet i titlen skal stå øverst."""
    results = _search(populated, q="redningsmidler")
    assert "redningsmidler" in results.hits[0].document.title.lower()


def test_resultat_indeholder_uddrag(populated):
    results = _search(populated, q="brandøvelse")
    assert results.hits
    assert results.hits[0].snippet


def test_soegning_paa_dokumentnummer(populated):
    """Praktikere søger ofte direkte på bekendtgørelsesnummer."""
    results = _search(populated, q="1290")
    assert results.total >= 1
    assert any(h.document.document_number == "1290" for h in results.hits)


# ---------------------------------------------------------------------------
# Filtre
# ---------------------------------------------------------------------------


def test_filter_paa_kategori(populated):
    results = _search(populated, categories=["brandsikkerhed"])
    assert results.total >= 1
    for hit in results.hits:
        assert "brandsikkerhed" in [c.category.slug for c in hit.document.category_links]


def test_filter_paa_myndighed(populated):
    results = _search(populated, authorities=["Søfartsstyrelsen"])
    assert results.total >= 1
    assert all(h.document.authority == "Søfartsstyrelsen" for h in results.hits)


def test_filter_paa_dokumenttype(populated):
    results = _search(populated, document_types=["Lovbekendtgørelse"])
    assert results.total >= 1
    assert all(h.document.document_type == "Lovbekendtgørelse" for h in results.hits)


def test_filter_paa_status(populated):
    """Gældende/ophævet skal kunne adskilles — centralt ved regelefterlevelse."""
    gaeldende = _search(populated, statuses=["Gældende"])
    historisk = _search(populated, statuses=["Historisk"])

    assert gaeldende.total >= 1
    assert historisk.total >= 1
    assert all(h.document.status == "Gældende" for h in gaeldende.hits)
    assert all(h.document.status == "Historisk" for h in historisk.hits)


def test_filter_paa_maritim_score(populated):
    results = _search(populated, min_score=88)
    assert results.total >= 1
    assert all(h.document.maritime_score >= 88 for h in results.hits)


def test_filter_paa_dato(populated):
    from datetime import date

    results = _search(populated, published_from=date(2024, 1, 1))
    assert results.total >= 1
    assert all(h.document.published_date >= date(2024, 1, 1) for h in results.hits)


def test_filtre_kombineres_med_og(populated):
    results = _search(
        populated, q="skib", authorities=["Søfartsstyrelsen"], statuses=["Gældende"]
    )
    for hit in results.hits:
        assert hit.document.authority == "Søfartsstyrelsen"
        assert hit.document.status == "Gældende"


# ---------------------------------------------------------------------------
# Sortering og sideinddeling
# ---------------------------------------------------------------------------


def test_sortering_paa_score(populated):
    results = _search(populated, sort="score_desc", page_size=20)
    scores = [h.document.maritime_score for h in results.hits]
    assert scores == sorted(scores, reverse=True)


def test_sortering_paa_dato(populated):
    results = _search(populated, sort="date_desc", page_size=20)
    datoer = [h.document.published_date for h in results.hits if h.document.published_date]
    assert datoer == sorted(datoer, reverse=True)


def test_sideinddeling(populated):
    side1 = _search(populated, page=1, page_size=5)
    side2 = _search(populated, page=2, page_size=5)

    assert len(side1.hits) == 5
    assert side1.total == side2.total == 15
    assert side1.total_pages == 3
    assert {h.document.id for h in side1.hits}.isdisjoint({h.document.id for h in side2.hits})


def test_soegeindeks_daekker_kategorinavne(populated):
    """Kategorinavne indgår i søgeteksten, så emnesøgning virker."""
    from app.core.text import fold

    document = populated.scalars(
        select(Document).where(Document.source_id == "FIXT-BEK-2023-0512")
    ).one()

    assert document.category_links
    for link in document.category_links:
        assert fold(link.category.name) in (document.search_text or "")


def test_soegefelter_gemmes_foldet(populated):
    """Foldning ved indeksering er det der gør danske tegn søgbare."""
    document = populated.scalars(
        select(Document).where(Document.source_id == "FIXT-BEK-2021-0455")
    ).one()

    assert "søulykke" not in (document.search_text or "")
    assert "soeulykke" in (document.search_text or "")
    assert document.search_title
    assert document.search_title == document.search_title.lower()

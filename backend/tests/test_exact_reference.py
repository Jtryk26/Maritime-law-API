"""Eksakte retskildehenvisninger: "BEK nr. 2207", "LBK 160", "2207".

Søger brugeren på et dokuments nummer, skal netop det dokument stå øverst
— også når det er en ophævelsesakt eller et støttedokument, som
domænereglerne ellers nedjusterer. Testene sætter hvert eksakt opslag op
mod et "konkurrerende" dokument, der ORGANISK ville vinde (højere score i
den almindelige rangering). Kun det deterministiske opslag kan derfor
forklare, at det efterspurgte dokument står først.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import ImportService
from app.services.ranking import LawClass
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient
from app.services.search import SearchQuery, get_search_backend
from app.services.search.reference import parse_source_reference

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "documents.json"

REPEAL_TITLE = (
    "Bekendtgørelse om ophævelse af visse bekendtgørelser og tekniske forskrifter "
    "på Søfartsstyrelsens område"
)


def _record(source_id, number, doc_type, published, title, content, status="Gældende"):
    return {
        "source_id": source_id,
        "retsinformation_id": source_id,
        "title": title,
        "short_title": None,
        "document_type": doc_type,
        "authority": "Søfartsstyrelsen",
        "ministry": "Erhvervsministeriet",
        "published_date": published,
        "effective_date": published,
        "status": status,
        "change_date": published,
        "document_number": number,
        "keywords": [],
        "source_url": f"https://example.invalid/fixture/{source_id}",
        "content": content,
    }


EXTRA_DOCUMENTS = [
    # Ophævelsesakten. Støttedokument med repeal_penalty og uden maritimt bidrag.
    _record(
        "TEST-BEK-2021-2207", "2207", "BEK", "2021-11-30", REPEAL_TITLE,
        "§ 1. Følgende bekendtgørelser og tekniske forskrifter ophæves: "
        "1) Bekendtgørelse nr. 301 om redningsmidler i skibe, "
        "2) Bekendtgørelse nr. 302 om brandsikkerhed i passagerskibe, og "
        "3) Teknisk forskrift nr. 3 om radioudstyr i skibe.\n"
        "§ 2. Bekendtgørelsen træder i kraft den 1. januar 2022.",
    ),
    # Konkurrent: en kernelov, der henviser til BEK nr. 2207 flere gange og
    # derfor står stærkt, når tallet søges som almindelig tekst.
    _record(
        "TEST-BEK-2022-1500", "1500", "BEK", "2022-03-01",
        "Bekendtgørelse om sikkerhed for passagerskibe i indenrigsfart",
        "§ 1. Bekendtgørelsen gælder for passagerskibe i indenrigsfart. Reglerne i "
        "den tidligere bekendtgørelse er ophævet ved BEK nr. 2207, jf. BEK nr. 2207 § 1.\n"
        "§ 2. Skibe skal have redningsmidler og brandsikkerhed svarende til de krav, "
        "der gjaldt før BEK nr. 2207.",
    ),
    # Støttedokument (ændringsbekendtgørelse) om olieforurening.
    _record(
        "TEST-BEK-2020-0886", "886", "BEK", "2020-06-15",
        "Bekendtgørelse om ændring af bekendtgørelse om beredskab mod olieforurening fra skibe",
        "§ 1. I bekendtgørelse om beredskab mod olieforurening fra skibe ændres § 3, "
        "stk. 2, så øvelser afholdes årligt.\n§ 2. Bekendtgørelsen træder i kraft "
        "den 1. juli 2020.",
    ),
    # Konkurrent: hovedreglen om olieforurening. Vinder organisk.
    _record(
        "TEST-BEK-2023-1600", "1600", "BEK", "2023-01-10",
        "Bekendtgørelse om beredskab mod olieforurening fra skibe",
        "§ 1. Skibe skal have en beredskabsplan mod olieforurening. "
        "Olieforurening skal straks indberettes.\n§ 2. Rederiet sikrer øvelser i "
        "bekæmpelse af olieforurening fra skibe og olieforurening i havne.",
    ),
]


def _fixture_records() -> list[dict]:
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return list(data if isinstance(data, list) else data.get("documents", data))


@pytest.fixture()
def populated(seeded_session):
    ImportService(
        seeded_session,
        client=FixtureRetsinformationClient(documents=_fixture_records() + EXTRA_DOCUMENTS),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()
    return seeded_session


def _search(session, q, mode="lexical", **kwargs):
    backend = get_search_backend(session, mode)
    return backend.search(session, SearchQuery(q=q, mode=mode, page_size=20, **kwargs))


def _numbers(results) -> list[str]:
    # Importeren gemmer typen som fuldt navn ("Bekendtgørelse"), så kun
    # nummeret sammenlignes. Alle testdokumenter her er bekendtgørelser.
    return [hit.document.document_number for hit in results.hits]


# ---------------------------------------------------------------------------
# Parseren
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,number,doc_type,year,remainder",
    [
        ("BEK nr. 2207", "2207", "BEK", None, ""),
        ("BEK 2207", "2207", "BEK", None, ""),
        ("bek.nr.2207", "2207", "BEK", None, ""),
        ("LBK 160", "160", "LBK", None, ""),
        ("Bekendtgørelse nr. 2207 af 30. november 2021", "2207", "BEK", 2021, ""),
        ("Bekendtgørelse nr. 2207 af 30/11/2021 brand", "2207", "BEK", 2021, "brand"),
        ("BEK 2207/2021", "2207", "BEK", 2021, ""),
        ("Lovbekendtgørelse nr. 33 sikkerhed til søs", "33", "LBK", None, "sikkerhed til søs"),
        ("LOV 160 af 2019", "160", "LOV", 2019, ""),
        ("Vejledning nr. 9004", "9004", "VEJ", None, ""),
        ("nr. 2207", "2207", None, None, ""),
        ("2207", "2207", None, None, ""),
        ("BEK 886 olieforurening", "886", "BEK", None, "olieforurening"),
        ("BEK nr. 0886 Ærø-færger", "886", "BEK", None, "Ærø-færger"),
    ],
)
def test_henvisningen_laeses(query, number, doc_type, year, remainder):
    reference = parse_source_reference(query)
    assert reference is not None
    assert (reference.number, reference.doc_type, reference.year, reference.remainder) == (
        number, doc_type, year, remainder,
    )


@pytest.mark.parametrize(
    "query",
    ["hviletid 24 timer", "lov om sikkerhed til søs", "2207 brand", "bekendtgørelse om brand", ""],
)
def test_almindelig_soegning_er_ikke_en_henvisning(query):
    assert parse_source_reference(query) is None


# ---------------------------------------------------------------------------
# Søgningen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["lexical", "hybrid"])
def test_bek_nr_2207_staar_oeverst_trods_ophaevelsesstraf(populated, mode):
    results = _search(populated, "BEK nr. 2207", mode=mode)

    first = results.hits[0]
    assert first.document.document_number == "2207"
    assert first.exact_match is True
    assert first.document.law_class == LawClass.SUPPORT

    # Straffene er stadig regnet ud — de afgør bare ikke placeringen.
    names = {a.name for a in first.ranking.adjustments}
    assert {"repeal_penalty", "support_penalty", "exact_reference"} <= names

    # Konkurrenten ville have vundet organisk ...
    competitor = next(h for h in results.hits if h.document.document_number == "1500")
    assert competitor.ranking.final_score > first.ranking.final_score
    # ... og står stadig på siden, fundet af den almindelige søgning.
    assert competitor.exact_match is False


def test_rent_tal_placerer_dokumentet_oeverst(populated):
    results = _search(populated, "2207")
    assert results.hits[0].document.document_number == "2207"
    assert results.hits[0].exact_match is True


def test_fuld_henvisning_med_dato(populated):
    results = _search(populated, "Bekendtgørelse nr. 2207 af 30. november 2021")
    assert results.hits[0].document.document_number == "2207"


def test_sammensat_soegning_finder_nummeret_foerst_og_teksten_bagefter(populated):
    results = _search(populated, "BEK 886 olieforurening")

    assert _numbers(results)[0] == "886"
    assert results.hits[0].exact_match is True
    # Resten af siden er den almindelige søgning på "olieforurening".
    assert "1600" in _numbers(results)
    assert all(not h.exact_match for h in results.hits[1:])


def test_uden_nummeret_vinder_hovedreglen(populated):
    """Kontrol: BEK 886 står IKKE først af sig selv. Kun opslaget flytter den."""
    results = _search(populated, "olieforurening")
    assert _numbers(results)[0] == "1600"
    assert all(not h.exact_match for h in results.hits)


def test_samme_nummer_flere_aar_nyeste_gaeldende_foerst(populated):
    """BEK 512 findes fra både 2023 og 2024 i fixtures."""
    results = _search(populated, "BEK 512")
    exact = [h for h in results.hits if h.exact_match]
    assert [h.document.published_date.year for h in exact] == [2024, 2023]
    assert results.hits[: len(exact)] == exact


def test_aarstal_indsnaevrer_til_eet_dokument(populated):
    results = _search(populated, "BEK 512/2023")
    exact = [h for h in results.hits if h.exact_match]
    assert len(exact) == 1
    assert exact[0].document.published_date.year == 2023


def test_forkert_type_er_ikke_et_eksakt_match(populated):
    results = _search(populated, "LOV 2207")
    assert not any(h.exact_match for h in results.hits)


def test_brugerens_filtre_gaelder_ogsaa_eksakte_opslag(populated):
    results = _search(populated, "BEK nr. 2207", law_classes=[LawClass.CORE])
    assert all(h.document.document_number != "2207" for h in results.hits)


def test_valgt_sortering_vinder_over_eksakt_placering(populated):
    """Sorterer brugeren selv, er der ingen "øverst" at placere dokumentet på."""
    results = _search(populated, "BEK nr. 2207", sort="date_desc")
    assert not any(h.exact_match for h in results.hits)


def test_api_markerer_eksakt_match(populated, api_client):
    response = api_client.get("/api/search", params={"q": "BEK nr. 2207"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["document_number"] == "2207"
    assert items[0]["exact_match"] is True
    assert all(item["exact_match"] is False for item in items[1:])

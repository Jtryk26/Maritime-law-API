"""API-kontrakten for det, brugerfladen blev omskrevet til at vise.

Frontenden må ikke skulle gætte: den korte titel, dokumentets rolle,
paragrafhittet, forklaringen på rækkefølgen og lovens struktur skal alle
komme fra API'et. Testene her låser netop de felter fast.
"""

from __future__ import annotations

import pytest

from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient


@pytest.fixture()
def api(api_client, seeded_session):
    ImportService(
        seeded_session,
        client=FixtureRetsinformationClient(revision=1),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()
    return api_client


class TestSoegesvar:
    def test_hit_baerer_begge_titler(self, api):
        item = api.get("/api/search", params={"q": "sikkerhed til søs"}).json()["items"][0]

        assert item["display_title"]
        assert item["original_title"] == item["title"]
        assert len(item["display_title"]) <= len(item["original_title"])

    def test_hit_baerer_dokumentets_rolle(self, api):
        item = api.get("/api/search", params={"q": "hviletid"}).json()["items"][0]

        assert item["law_class"] in {"kernelaw", "speciallaw", "support"}
        assert item["law_class_label"]
        assert 0.0 <= item["scope_score"] <= 1.0
        assert 0.0 <= item["authority_score"] <= 1.0

    def test_hit_peger_paa_en_paragraf_med_kapitel(self, api):
        item = api.get("/api/search", params={"q": "hviletid"}).json()["items"][0]
        paragraph = item["paragraph"]

        assert paragraph is not None
        assert paragraph["paragraph_id"].startswith("§")
        assert paragraph["chapter_no"]
        assert paragraph["legal_path"]
        assert paragraph["full_citation"]
        assert paragraph["snippet"]

    def test_rangeringen_kan_forklares(self, api):
        item = api.get("/api/search", params={"q": "hviletid"}).json()["items"][0]
        ranking = item["ranking"]

        assert ranking is not None
        assert ranking["final_score"] > 0
        assert ranking["base_score"] > 0
        for adjustment in ranking["adjustments"]:
            assert adjustment["reason"]
            assert isinstance(adjustment["percent"], int)

    def test_svaret_siger_hvordan_soegningen_blev_laest(self, api):
        payload = api.get("/api/search", params={"q": "grønlandske lodser hviletid"}).json()
        intent = payload["intent"]

        assert intent["kind"] == "niche"
        assert set(intent["niche_groups"]) == {"groenland", "lodseri"}
        # Læsbare navne følger med: brugerfladen må aldrig vise slugs.
        assert set(intent["niche_labels"]) == {"Grønland", "Lodseri"}
        assert intent["label"]

    def test_filter_paa_dokumentklasse_haandhaeves_i_api(self, api):
        payload = api.get(
            "/api/search", params={"q": "hviletid", "law_class": "speciallaw"}
        ).json()

        assert payload["items"]
        assert all(item["law_class"] == "speciallaw" for item in payload["items"])
        assert payload["applied_filters"]["law_classes"] == ["speciallaw"]

    def test_ukendt_dokumentklasse_ignoreres_frem_for_at_fejle(self, api):
        """Et ugyldigt filter må ikke give 500 — men det må heller ikke
        stiltiende filtrere på noget andet."""
        payload = api.get(
            "/api/search", params={"q": "hviletid", "law_class": "opdigtet"}
        ).json()
        assert payload["applied_filters"]["law_classes"] == []


class TestForside:
    def test_kernelove_kan_hentes(self, api):
        items = api.get("/api/core-laws").json()

        assert items
        assert all(item["law_class"] == "kernelaw" for item in items)
        assert all(item["status"] == "Gældende" for item in items)
        assert all(item["is_maritime"] for item in items)

    def test_kernelove_respekterer_graensen(self, api):
        assert len(api.get("/api/core-laws", params={"limit": 3}).json()) == 3

    def test_facetter_indeholder_dokumentklasser(self, api):
        facets = api.get("/api/facets").json()
        classes = {c["value"]: c for c in facets["law_classes"]}

        assert "kernelaw" in classes
        assert classes["kernelaw"]["count"] > 0
        assert classes["kernelaw"]["description"]


class TestDokumentstruktur:
    def _document_id(self, api) -> int:
        return api.get("/api/search", params={"q": "brandsikkerhed"}).json()["items"][0]["id"]

    def test_detaljesvaret_indeholder_strukturen(self, api):
        body = api.get(f"/api/documents/{self._document_id(api)}").json()
        structure = body["structure"]

        assert structure["has_paragraphs"] is True
        assert structure["paragraph_count"] > 0
        assert structure["chapters"]
        assert structure["chapters"][0]["paragraphs"]

    def test_paragraffernes_fulde_ordlyd_foelger_med(self, api):
        """Læsevisningen sættes af strukturen. Et afkortet uddrag ville
        betyde, at brugeren læste en forkortet lovtekst uden at vide det."""
        body = api.get(f"/api/documents/{self._document_id(api)}").json()
        paragraph = body["structure"]["chapters"][0]["paragraphs"][0]

        assert paragraph["text"]
        assert paragraph["text"] in body["content"]
        assert not paragraph["text"].endswith("…")

    def test_struktur_kan_hentes_alene(self, api):
        document_id = self._document_id(api)
        alone = api.get(f"/api/documents/{document_id}/structure").json()
        embedded = api.get(f"/api/documents/{document_id}").json()["structure"]

        assert alone == embedded

    def test_ukendt_dokument_giver_404(self, api):
        assert api.get("/api/documents/9999/structure").status_code == 404

    def test_detaljesvaret_baerer_begge_titler(self, api):
        body = api.get(f"/api/documents/{self._document_id(api)}").json()
        assert body["display_title"]
        assert body["original_title"] == body["title"]

"""API-test af søgetilstande, lignende dokumenter og søgelog."""

from __future__ import annotations

import pytest


@pytest.fixture()
def api(api_client, indexed_session):
    """Testklient mod en database med importerede OG vektoriserede dokumenter."""
    return api_client


@pytest.fixture()
def api_uden_vektorer(api_client, seeded_session):
    """Importeret, men ikke vektoriseret."""
    from app.services.categorization import KeywordCategorizationEngine
    from app.services.importer import ImportService
    from app.services.relevance import KeywordRelevanceEngine
    from app.services.retsinformation import FixtureRetsinformationClient

    ImportService(
        seeded_session,
        client=FixtureRetsinformationClient(revision=1),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()
    return api_client


class TestSoegetilstande:
    def test_standard_er_hybrid_naar_der_findes_vektorer(self, api):
        response = api.get("/api/search", params={"q": "brand passagerskib"})
        assert response.status_code == 200

        payload = response.json()
        assert payload["mode"] == "hybrid"
        assert payload["semantic_available"] is True
        assert payload["total"] >= 1

    def test_leksikalsk_kan_vaelges_eksplicit(self, api):
        payload = api.get(
            "/api/search", params={"q": "brand passagerskib", "mode": "lexical"}
        ).json()
        assert payload["mode"] == "lexical"
        assert all(item["match_source"] == "lexical" for item in payload["items"])

    def test_semantisk_kan_vaelges_eksplicit(self, api):
        payload = api.get("/api/search", params={"q": "redningsmidler", "mode": "semantic"}).json()
        assert payload["mode"] == "semantic"
        for item in payload["items"]:
            assert item["semantic_score"] is not None
            assert 0.0 <= item["semantic_score"] <= 1.0

    def test_ugyldig_tilstand_afvises_af_validering(self, api):
        assert api.get("/api/search", params={"q": "skib", "mode": "telepati"}).status_code == 422

    def test_uden_vektorer_nedgraderes_der_synligt(self, api_uden_vektorer):
        """Nedgraderingen står i svaret. En bruger, der tror der blev søgt
        på betydning, ville ellers kunne konkludere at emnet er ureguleret."""
        payload = api_uden_vektorer.get(
            "/api/search", params={"q": "brand", "mode": "hybrid"}
        ).json()

        assert payload["mode"] == "lexical"
        assert payload["semantic_available"] is False
        assert payload["notice"]
        assert "embed run" in payload["notice"]

    def test_hit_forklarer_hvorfor_det_er_med(self, api):
        payload = api.get("/api/search", params={"q": "brandsikkerhed passagerskibe"}).json()
        assert payload["items"]

        item = payload["items"][0]
        assert item["match_source"] in {"lexical", "semantic", "both"}
        assert "rank" in item and "snippet" in item

    def test_filtre_virker_sammen_med_hybrid(self, api):
        payload = api.get(
            "/api/search",
            params={"q": "skib", "mode": "hybrid", "document_type": "Bekendtgørelse"},
        ).json()
        assert all(item["document_type"] == "Bekendtgørelse" for item in payload["items"])

    def test_juridisk_forbehold_er_med_i_alle_tilstande(self, api):
        for mode in ("lexical", "semantic", "hybrid"):
            payload = api.get("/api/search", params={"q": "skib", "mode": mode}).json()
            assert "Retsinformation" in payload["legal_notice"]


class TestLignendeDokumenter:
    def test_finder_beslaegtede_dokumenter(self, api):
        first = api.get("/api/search", params={"q": "brandsikkerhed", "mode": "lexical"}).json()
        document_id = first["items"][0]["id"]

        response = api.get(f"/api/documents/{document_id}/similar")
        assert response.status_code == 200

        payload = response.json()
        assert all(item["id"] != document_id for item in payload)
        for item in payload:
            assert 0.0 <= item["similarity"] <= 1.0001

    def test_ukendt_dokument_giver_404(self, api):
        assert api.get("/api/documents/999999/similar").status_code == 404

    def test_uden_vektorer_er_tom_liste_ikke_fejl(self, api_uden_vektorer):
        """Manglende indeks er en driftstilstand, ikke en serverfejl."""
        response = api_uden_vektorer.get("/api/documents/1/similar")
        assert response.status_code == 200
        assert response.json() == []


class TestSoegelog:
    def test_soegninger_logges_og_kan_hentes(self, api):
        api.get("/api/search", params={"q": "redningsflåder om bord"})
        api.get("/api/search", params={"q": "redningsflåder om bord"})

        payload = api.get("/api/search/queries", params={"kind": "popular"}).json()
        assert payload
        assert payload[0]["query"] == "redningsflåder om bord"
        assert payload[0]["occurrences"] == 2

    def test_soegninger_uden_resultat_kan_listes(self, api):
        # Bevidst leksikalsk: testene kører med hash-udbyderen, hvis
        # ligheder ikke kan tærskles meningsfuldt (se hashing.py). Med en
        # rigtig model filtrerer den semantiske grænse en meningsløs
        # søgning fra, og listen virker også i hybrid.
        api.get("/api/search", params={"q": "kvantemekanisk vandpolo", "mode": "lexical"})

        payload = api.get("/api/search/queries", params={"kind": "without_results"}).json()
        assert any(item["query"] == "kvantemekanisk vandpolo" for item in payload)

    def test_relaterede_soegninger_kommer_med_i_svaret(self, api):
        api.get("/api/search", params={"q": "brand på passagerskib"})
        payload = api.get("/api/search", params={"q": "brand om bord på passagerskib"}).json()

        assert any(r["query"] == "brand på passagerskib" for r in payload["related_queries"])

    def test_relaterede_kan_slaas_fra_pr_kald(self, api):
        api.get("/api/search", params={"q": "brand på passagerskib"})
        payload = api.get(
            "/api/search", params={"q": "brand om bord", "related": "false"}
        ).json()
        assert payload["related_queries"] == []

    def test_selvstaendigt_endpoint_for_relaterede(self, api):
        api.get("/api/search", params={"q": "lodspligt i danske farvande"})
        payload = api.get("/api/search/related", params={"q": "lodspligt farvande"}).json()
        assert isinstance(payload, list)

    def test_ugyldig_type_afvises(self, api):
        assert api.get("/api/search/queries", params={"kind": "alt"}).status_code == 422


class TestDriftsvisning:
    def test_stats_indeholder_vektortilstand(self, api):
        payload = api.get("/api/stats").json()

        embeddings = payload["embeddings"]
        assert embeddings["enabled"] is True
        assert embeddings["embedded_documents"] > 0
        assert embeddings["chunks"] > 0
        # Hash-udbyderen må ikke udgive sig for at være semantisk.
        assert embeddings["semantic"] is False

    def test_stats_indeholder_soegelog(self, api):
        api.get("/api/search", params={"q": "skibssikkerhed"})
        payload = api.get("/api/stats").json()

        assert payload["search_log"]["distinct_queries"] >= 1
        assert payload["search_log"]["total_searches"] >= 1

    def test_embeddings_status_endpoint(self, api):
        payload = api.get("/api/embeddings/status").json()
        assert payload["enabled"] is True
        assert payload["coverage_pct"] == pytest.approx(100.0, abs=0.1)

    def test_import_efterlader_arbejde_til_vektorisering(self, api_uden_vektorer):
        payload = api_uden_vektorer.get("/api/embeddings/status").json()
        assert payload["pending_documents"] > 0
        assert payload["embedded_documents"] == 0

    def test_vektorisering_kan_startes_via_api(self, api_uden_vektorer):
        response = api_uden_vektorer.post("/api/embeddings/run", json={"limit": 50})
        assert response.status_code == 201

        payload = response.json()
        assert payload["documents_embedded"] > 0
        assert payload["chunks_written"] > 0
        assert payload["pending_after"] == 0

        after = api_uden_vektorer.get("/api/embeddings/status").json()
        assert after["embedded_documents"] > 0

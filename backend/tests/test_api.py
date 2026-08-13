"""Test af REST-API'et."""

from __future__ import annotations

import pytest


@pytest.fixture()
def populated_api(api_client):
    """API med fixturdata importeret."""
    response = api_client.post(
        "/api/import/run", json={"source_client": "fixture", "fixture_revision": 1}
    )
    assert response.status_code == 201
    return api_client


# ---------------------------------------------------------------------------
# Systemtilstand
# ---------------------------------------------------------------------------


def test_health(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_openapi_dokumentation_genereres(api_client):
    assert api_client.get("/openapi.json").status_code == 200


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_import_via_api(api_client):
    response = api_client.post("/api/import/run", json={"source_client": "fixture"})
    assert response.status_code == 201

    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["documents_created"] == 15
    assert body["documents_rejected"] == 3
    assert body["used_synthetic_data"] is True


def test_ugyldig_kilde_afvises(api_client):
    response = api_client.post("/api/import/run", json={"source_client": "opdigtet"})
    assert response.status_code == 422


def test_importhistorik(populated_api):
    response = populated_api.get("/api/import/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["items"][0]["documents_checked"] == 18


def test_ukendt_importkoersel_giver_404(api_client):
    assert api_client.get("/api/import/runs/9999").status_code == 404


# ---------------------------------------------------------------------------
# Statistik
# ---------------------------------------------------------------------------


def test_stats(populated_api):
    body = populated_api.get("/api/stats").json()
    assert body["documents_total"] == 15
    assert body["documents_maritime"] == 15
    assert body["documents_synthetic"] == 15
    assert body["versions_total"] == 15
    assert body["categories_total"] >= 23
    assert body["last_import"]["status"] == "COMPLETED"
    assert body["top_categories"]


# ---------------------------------------------------------------------------
# Kategorier og facetter
# ---------------------------------------------------------------------------


def test_kategorier_med_taellinger(populated_api):
    body = populated_api.get("/api/categories").json()
    assert len(body) >= 23
    slugs = {c["slug"] for c in body}
    assert {"brandsikkerhed", "passagerskibe", "miljo-marpol"} <= slugs
    assert any(c["document_count"] > 0 for c in body)


def test_facetter_leverer_filtermuligheder(populated_api):
    body = populated_api.get("/api/facets").json()
    assert any(f["value"] == "Søfartsstyrelsen" for f in body["authorities"])
    assert any(f["value"] == "Bekendtgørelse" for f in body["document_types"])
    assert any(f["value"] == "Gældende" for f in body["statuses"])


# ---------------------------------------------------------------------------
# Søgning
# ---------------------------------------------------------------------------


def test_soegning(populated_api):
    body = populated_api.get("/api/search", params={"q": "brand passagerskib"}).json()
    assert body["total"] >= 1
    hit = body["items"][0]
    assert "brandsikkerhed" in hit["title"].lower()
    assert hit["maritime_score"] > 0
    assert hit["categories"]
    assert hit["snippet"]
    assert body["legal_notice"]


def test_soegning_med_filtre(populated_api):
    body = populated_api.get(
        "/api/search",
        params={"category": "brandsikkerhed", "status": "Gældende", "min_score": 50},
    ).json()
    assert body["total"] >= 1
    assert body["applied_filters"]["categories"] == ["brandsikkerhed"]
    for item in body["items"]:
        assert item["status"] == "Gældende"
        assert item["maritime_score"] >= 50


def test_soegning_paa_dokumentnummer(populated_api):
    body = populated_api.get("/api/search", params={"document_number": "1290"}).json()
    assert body["total"] == 1
    assert body["items"][0]["document_number"] == "1290"


def test_soegning_validerer_parametre(populated_api):
    assert populated_api.get("/api/search", params={"min_score": 500}).status_code == 422
    assert populated_api.get("/api/search", params={"page": 0}).status_code == 422
    assert populated_api.get("/api/search", params={"sort": "vilkårlig"}).status_code == 422


def test_sideinddeling_i_svaret(populated_api):
    body = populated_api.get("/api/search", params={"page_size": 5}).json()
    assert len(body["items"]) == 5
    assert body["total_pages"] == 3


# ---------------------------------------------------------------------------
# Dokumenter
# ---------------------------------------------------------------------------


def test_dokumentliste(populated_api):
    body = populated_api.get("/api/documents").json()
    assert body["total"] == 15
    assert body["items"]


def test_dokumentdetaljer_indeholder_alt_brugerfladen_skal_vise(populated_api):
    doc_id = populated_api.get("/api/search", params={"q": "brandsikkerhed"}).json()["items"][0]["id"]
    body = populated_api.get(f"/api/documents/{doc_id}").json()

    # Metadata
    assert body["title"]
    assert body["retsinformation_id"]
    assert body["document_type"] == "Bekendtgørelse"
    assert body["authority"] == "Søfartsstyrelsen"
    assert body["published_date"]
    assert body["status"] == "Gældende"
    # Indhold og klassifikation
    assert "§ 1." in body["content"]
    assert body["categories"]
    assert body["versions"]
    assert body["change_log"]
    # Sporbarhed og forbehold
    assert body["source_url"]
    assert body["last_retrieved_at"]
    assert body["legal_notice"]
    assert body["synthetic_notice"], "syntetiske data skal markeres eksplicit"
    assert body["normalized_metadata"] is not None
    assert body["source_metadata"] is not None


def test_relevansforklaring_er_fuldt_reviderbar(populated_api):
    """Systemet må ikke være en sort boks, når det gælder lovgivning."""
    doc_id = populated_api.get("/api/search", params={"q": "brandsikkerhed"}).json()["items"][0]["id"]
    relevance = populated_api.get(f"/api/documents/{doc_id}").json()["relevance"]

    assert relevance["engine"] == "keyword"
    assert relevance["is_maritime"] is True
    assert relevance["classification"] == "maritime"
    assert relevance["reason"]
    assert relevance["matched_terms"]
    assert relevance["concepts"]
    assert relevance["matches"]

    match = relevance["matches"][0]
    assert {"term", "field", "occurrences", "counted_occurrences",
            "term_weight", "field_weight", "contribution"} <= set(match)

    beregning = relevance["calculation"]
    assert beregning["raw_score"] > 0
    assert beregning["field_contributions"]
    assert beregning["thresholds"]["maritime"] == 60

    # Vurderingen skal kunne henføres til en konkret version.
    assert relevance["evaluated_version_number"] == 1
    assert relevance["is_stale"] is False


def test_versionshistorik(populated_api):
    doc_id = populated_api.get("/api/search", params={"q": "brandsikkerhed"}).json()["items"][0]["id"]
    versions = populated_api.get(f"/api/documents/{doc_id}/versions").json()

    assert len(versions) == 1
    assert versions[0]["version_number"] == 1
    assert versions[0]["is_current"] is True
    assert versions[0]["content_hash"]


def test_historisk_version_kan_hentes_efter_aendring(populated_api):
    """Efter en indholdsændring skal version 1 stadig kunne læses."""
    populated_api.post(
        "/api/import/run", json={"source_client": "fixture", "fixture_revision": 2}
    )
    doc_id = populated_api.get(
        "/api/search", params={"q": "brandsikkerhed"}
    ).json()["items"][0]["id"]

    versions = populated_api.get(f"/api/documents/{doc_id}/versions").json()
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["is_current"] is True

    v1 = populated_api.get(f"/api/documents/{doc_id}/versions/1").json()
    v2 = populated_api.get(f"/api/documents/{doc_id}/versions/2").json()
    assert "termisk kamera" not in v1["content"]
    assert "termisk kamera" in v2["content"]
    assert v1["content_hash"] != v2["content_hash"]


def test_statusaendring_afspejles_i_dokumentet(populated_api):
    populated_api.post(
        "/api/import/run", json={"source_client": "fixture", "fixture_revision": 2}
    )
    body = populated_api.get("/api/search", params={"q": "lodspligt"}).json()
    assert body["items"][0]["status"] == "Ophævet"


def test_ukendt_dokument_giver_404(populated_api):
    response = populated_api.get("/api/documents/999999")
    assert response.status_code == 404
    assert response.json()["error_type"] == "http_error"


def test_ukendt_version_giver_404(populated_api):
    doc_id = populated_api.get("/api/documents").json()["items"][0]["id"]
    assert populated_api.get(f"/api/documents/{doc_id}/versions/99").status_code == 404

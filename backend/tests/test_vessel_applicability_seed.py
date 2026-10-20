"""Fase 1-seedregler mod verifikationsmatricen.

Reglerne læses fra ``data/fixtures/vessel_applicability_seed.json`` — den
samme fil, der skal udkastes i et rigtigt miljø — udkastes og godkendes via
API'et og afprøves mod tre profiler. Matricen er en påstand om motoren, ikke
om lovens indhold: henvisningerne er ikke efterprøvet af en jurist.
"""

from __future__ import annotations

import json

import pytest

from app.services.vessel_applicability import evaluation_cache
from tests.conftest import REPO_ROOT

BASE = "/api/v1/applicability"
SEED = json.loads(
    (REPO_ROOT / "data" / "fixtures" / "vessel_applicability_seed.json").read_text("utf-8")
)

PAX_SURVEY = "DK-MEDD-B-CAP-I-REG-7-PASSENGER-SURVEY"
CARGO_SURVEY = "DK-MEDD-B-CAP-I-REG-8-CARGO-SURVEY-500GT"
SAFETY_ORG = "DK-BEK-2017-863-SEC-4-SAFETY-ORGANIZATION"

COMMON = {
    "flag_state": "DK",
    "operating_area": "international",
    "keel_laid_date": "2010-01-01",
    "assessment_date": "2026-10-20",
}

FERRY = {**COMMON, "vessel_name": "A: Passagerfærge", "primary_type": "passenger",
         "gross_tonnage": 3200, "max_passengers": 420, "crew_capacity": 22}
COASTER = {**COMMON, "vessel_name": "B: Coaster", "primary_type": "cargo",
           "gross_tonnage": 1400, "max_passengers": 0, "crew_capacity": 8}
INCOMPLETE = {**COMMON, "vessel_name": "C: Ufuldstændig", "primary_type": "cargo",
              "gross_tonnage": 450, "crew_capacity": 18, "max_passengers": None}


@pytest.fixture()
def seeded_client(api_client):
    evaluation_cache.invalidate_all()
    for draft in SEED["drafts"]:
        response = api_client.post(f"{BASE}/rules/draft", json=draft)
        assert response.status_code == 201, response.text
        rule_id = draft["rule"]["rule_id"]
        response = api_client.post(
            f"{BASE}/rules/{rule_id}/approve",
            json={
                "reviewer_id": "test-reviewer",
                "signature": "test",
                "content_hash": response.json()["content_hash"],
            },
        )
        assert response.status_code == 200, response.text
    yield api_client
    evaluation_cache.invalidate_all()


def _results(client, profile: dict) -> dict[str, dict]:
    response = client.post(f"{BASE}/evaluate", json=profile)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["rules_evaluated"] == 3
    return {r["rule_id"]: r for t in report["topics"] for r in t["results"]}


def _decided_at(result: dict) -> str | None:
    return next((s["step"] for s in result["steps"] if s["outcome"] == "decided"), None)


def test_seedfilen_er_kun_udkast():
    assert {d["rule"]["status"] for d in SEED["drafts"]} == {"DRAFT"}


def test_a_passagerfaerge(seeded_client):
    results = _results(seeded_client, FERRY)

    assert results[PAX_SURVEY]["verdict"] == "APPLIES"
    assert results[SAFETY_ORG]["verdict"] == "APPLIES"

    cargo = results[CARGO_SURVEY]
    assert cargo["verdict"] == "DOES_NOT_APPLY"
    # Afgjort af passagerundtagelsen, før inklusionen blev taget i betragtning.
    assert _decided_at(cargo) == "exclusions"
    assert cargo["reasons"][0].startswith("Undtaget: max_passengers gt 12")


def test_b_coaster(seeded_client):
    results = _results(seeded_client, COASTER)

    assert results[CARGO_SURVEY]["verdict"] == "APPLIES"
    assert results[PAX_SURVEY]["verdict"] == "DOES_NOT_APPLY"

    safety = results[SAFETY_ORG]
    assert safety["verdict"] == "DOES_NOT_APPLY"
    assert safety["missing_fields"] == []
    assert _decided_at(safety) == "inclusions"
    crew = next(c for c in safety["conditions"] if c["field"] == "crew_capacity")
    assert crew["actual_value"] == 8
    assert crew["outcome"] == "NOT_MET"


def test_c_ufuldstaendig_profil_gaetter_ikke_paa_nul(seeded_client):
    results = _results(seeded_client, INCOMPLETE)

    pax = results[PAX_SURVEY]
    assert pax["verdict"] == "NEEDS_MANUAL_REVIEW"
    assert pax["missing_fields"] == ["max_passengers"]

    # 450 BT < 500 er beviseligt ikke opfyldt; den ukendte passagerundtagelse
    # kan da ikke ændre svaret.
    cargo = results[CARGO_SURVEY]
    assert cargo["verdict"] == "DOES_NOT_APPLY"
    assert cargo["missing_fields"] == []
    assert _decided_at(cargo) == "inclusions"

    assert results[SAFETY_ORG]["verdict"] == "APPLIES"


def test_c_udeladt_felt_giver_samme_svar_som_null(seeded_client):
    omitted = {k: v for k, v in INCOMPLETE.items() if k != "max_passengers"}
    a = _results(seeded_client, INCOMPLETE)
    b = _results(seeded_client, omitted)
    assert {k: v["verdict"] for k, v in a.items()} == {k: v["verdict"] for k, v in b.items()}

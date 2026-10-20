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

PAX_SURVEY = "SOLAS-CAP-I-REG-7-PASSENGER-SURVEY"
CARGO_SURVEY = "SOLAS-CAP-I-REG-8-CARGO-SURVEY-500GT"
SAFETY_ORG = "DK-BEK-2017-863-SEC-4-SAFETY-ORG"

COMMON = {
    "flag_state": "DK",
    "keel_laid_date": "2010-01-01",
    "assessment_date": "2026-10-20",
}

FERRY = {**COMMON, "vessel_name": "A: Passagerfærge", "primary_type": "passenger",
         "operating_area": "international", "gross_tonnage": 3200,
         "max_passengers": 420, "actual_crew_count": 22}
COASTER = {**COMMON, "vessel_name": "B: Coaster", "primary_type": "cargo",
           "operating_area": "international", "gross_tonnage": 1400,
           "max_passengers": 0, "actual_crew_count": 8}
DOMESTIC = {**COMMON, "vessel_name": "C: Indenrigs lastskib", "primary_type": "cargo",
            "operating_area": "domestic", "gross_tonnage": 450,
            "max_passengers": None, "actual_crew_count": None}


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


def _condition(result: dict, field: str, kind: str = "inclusion") -> dict:
    return next(c for c in result["conditions"] if c["field"] == field and c["kind"] == kind)


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
    assert results[SAFETY_ORG]["verdict"] == "APPLIES"
    assert _condition(results[SAFETY_ORG], "actual_crew_count")["actual_value"] == 8

    pax = results[PAX_SURVEY]
    assert pax["verdict"] == "DOES_NOT_APPLY"
    assert pax["missing_fields"] == []
    assert _decided_at(pax) == "inclusions"


def test_c_indenrigs_lastskib_med_manglende_besaetning(seeded_client):
    results = _results(seeded_client, DOMESTIC)

    # Indenrigsfart falder beviseligt uden for; det ukendte passagertal kan
    # da ikke ændre svaret og nævnes ikke som manglende.
    pax = results[PAX_SURVEY]
    assert pax["verdict"] == "DOES_NOT_APPLY"
    assert pax["missing_fields"] == []
    assert _condition(pax, "operating_area")["outcome"] == "NOT_MET"

    cargo = results[CARGO_SURVEY]
    assert cargo["verdict"] == "DOES_NOT_APPLY"
    assert cargo["missing_fields"] == []
    assert _condition(cargo, "operating_area")["outcome"] == "NOT_MET"
    assert _condition(cargo, "gross_tonnage")["outcome"] == "NOT_MET"

    safety = results[SAFETY_ORG]
    assert safety["verdict"] == "NEEDS_MANUAL_REVIEW"
    assert safety["missing_fields"] == ["actual_crew_count"]


def test_koejekapacitet_udloeser_ikke_sikkerhedsorganisationen(seeded_client):
    """Køjepladser er ikke ansatte: 20 køjer og ukendt besætning er ukendt."""
    profile = {**DOMESTIC, "crew_capacity": 20}
    safety = _results(seeded_client, profile)[SAFETY_ORG]
    assert safety["verdict"] == "NEEDS_MANUAL_REVIEW"
    assert safety["missing_fields"] == ["actual_crew_count"]


@pytest.mark.parametrize("excluded_type", ["fishing", "warship", "pleasure_yacht"])
def test_solas_undtagelser(seeded_client, excluded_type):
    profile = {**FERRY, "primary_type": excluded_type}
    pax = _results(seeded_client, profile)[PAX_SURVEY]
    assert pax["verdict"] == "DOES_NOT_APPLY"
    assert _decided_at(pax) == "exclusions"


def test_udeladt_felt_giver_samme_svar_som_null(seeded_client):
    omitted = {k: v for k, v in DOMESTIC.items() if v is not None}
    a = _results(seeded_client, DOMESTIC)
    b = _results(seeded_client, omitted)
    assert {k: v["verdict"] for k, v in a.items()} == {k: v["verdict"] for k, v in b.items()}

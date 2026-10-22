"""Fase 1-seedregler mod verifikationsmatricen.

Reglerne læses fra testmatricen ``backend/tests/data/vessel_rules_matrix.yaml``
— samme format som ``vessel-rules ingest`` — efterprøves mod høstede testdokumenter,
godkendes og afprøves gennem den offentlige API mod tre profiler. Matricen er
en påstand om motoren, ikke om lovens indhold.
"""

from __future__ import annotations

import pytest

from app.services.vessel_applicability import evaluation_cache
from tests.vessel_rules_support import (
    CARGO_SURVEY,
    PAX_SURVEY,
    REVIEWER,
    SAFETY_ORG,
    SEED,
    seed_and_approve,
)

BASE = "/api/v1/applicability"

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
def seeded_client(public_api_client, session):
    """Den offentlige klient — uden token — mod tre godkendte seedregler."""
    evaluation_cache.invalidate_all()
    seed_and_approve(session)
    yield public_api_client
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


def test_seedfilen_angiver_ingen_status():
    assert all("status" not in d.rule.model_fields_set for d in SEED.drafts)


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


# ---------------------------------------------------------------------------
# Fremdrift og bygning (SOLAS kap. I, regel 3(a)(iii) og (iv))
# ---------------------------------------------------------------------------


def test_skib_uden_mekanisk_fremdrift_er_undtaget(seeded_client):
    profile = {**FERRY, "propulsion_type": "non_mechanical"}
    pax = _results(seeded_client, profile)[PAX_SURVEY]
    assert pax["verdict"] == "DOES_NOT_APPLY"
    assert _decided_at(pax) == "exclusions"


def test_primitivt_bygget_traeskib_er_undtaget(seeded_client):
    profile = {**COASTER, "is_primitive_wooden_build": True}
    cargo = _results(seeded_client, profile)[CARGO_SURVEY]
    assert cargo["verdict"] == "DOES_NOT_APPLY"
    assert _decided_at(cargo) == "exclusions"


def test_sejlskib_med_hjaelpemotor_er_ikke_undtaget(seeded_client):
    profile = {**FERRY, "propulsion_type": "sail_auxiliary"}
    assert _results(seeded_client, profile)[PAX_SURVEY]["verdict"] == "APPLIES"


def test_antaget_standard_fremgaar_af_sporet(seeded_client):
    response = seeded_client.post(f"{BASE}/evaluate", json=FERRY)
    report = response.json()
    assert report["assumed_defaults"] == ["is_primitive_wooden_build", "propulsion_type"]
    assert "propulsion_type" not in report["supplied_fields"]

    pax = {r["rule_id"]: r for t in report["topics"] for r in t["results"]}[PAX_SURVEY]
    propulsion = _condition(pax, "propulsion_type", kind="exclusion")
    assert propulsion["assumed_default"] is True
    assert propulsion["field_supplied"] is False
    assert propulsion["actual_value"] == "mechanical"
    assert "antaget standardværdi" in propulsion["detail"]


def test_oplyst_fremdrift_er_ikke_antaget(seeded_client):
    profile = {**FERRY, "propulsion_type": "mechanical"}
    report = seeded_client.post(f"{BASE}/evaluate", json=profile).json()
    assert report["assumed_defaults"] == ["is_primitive_wooden_build"]
    assert "propulsion_type" in report["supplied_fields"]


# ---------------------------------------------------------------------------
# Faste værdilister
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("primary_type", "fishing_vessel"),
        ("primary_type", "yacht"),
        ("operating_area", "a1_a4"),
        ("propulsion_type", "diesel"),
    ],
)
def test_profil_med_ukendt_vaerdi_afvises(seeded_client, field, value):
    response = seeded_client.post(f"{BASE}/evaluate", json={**FERRY, field: value})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Lovtekstens hash og integritet
# ---------------------------------------------------------------------------


def test_betydningsbaerende_tegn_bevares():
    """NFC, ikke NFKC: m² og m2 er ikke samme tekst."""
    from app.services.vessel_applicability import canonicalize_legal_text

    assert canonicalize_legal_text("areal på 10 m²") != canonicalize_legal_text("areal på 10 m2")
    assert canonicalize_legal_text("å") == "å"


def test_rettet_lovtekst_efter_godkendelse_afgoeres_ikke(seeded_client, session):
    from sqlalchemy import select

    from app.models import VesselApplicabilityRule

    row = session.scalar(
        select(VesselApplicabilityRule).where(VesselApplicabilityRule.rule_id == SAFETY_ORG)
    )
    row.source_text = row.source_text.replace("mindst 5", "mindst 50")
    session.commit()

    safety = _results(seeded_client, FERRY)[SAFETY_ORG]
    assert safety["verdict"] == "NEEDS_MANUAL_REVIEW"
    assert "lovtekst" in safety["reasons"][0]

    approved = {r["rule_id"]: r for r in seeded_client.get(f"{BASE}/rules/approved").json()}
    assert approved[SAFETY_ORG]["integrity_verified"] is False
    assert approved[PAX_SURVEY]["integrity_verified"] is True


def test_godkendte_regler_kan_kontrolleres_uden_token(seeded_client, public_api_client):
    response = public_api_client.get(f"{BASE}/rules/approved")
    assert response.status_code == 200
    rules = response.json()
    assert {r["rule_id"] for r in rules} == {PAX_SURVEY, CARGO_SURVEY, SAFETY_ORG}
    for rule in rules:
        assert rule["status"] == "APPROVED"
        assert rule["approved_by"] == REVIEWER
        assert len(rule["approved_content_hash"]) == 64
        assert rule["integrity_verified"] is True


def test_udkast_vises_ikke_som_godkendt(public_api_client, session):
    from tests.vessel_rules_support import ingest, seed_source_documents

    seed_source_documents(session)
    ingest(session)
    assert public_api_client.get(f"{BASE}/rules/approved").json() == []

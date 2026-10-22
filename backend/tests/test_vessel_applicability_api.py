"""Vessel Applicability 2.0 — den offentlige API, cachen og integriteten.

Den vigtigste påstand: kun godkendte, efterprøvede regler med uændret indhold
indgår i en vurdering — og API'et kan ikke ændre en regel. Regler skabes her
på samme måde som ``vessel-rules ingest``/``approve``, via tjenestelaget.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.services.vessel_applicability import evaluation_cache
from app.services.vessel_applicability.batch import DraftBatch
from tests.vessel_rules_support import approve, ingest, seed_source_documents

BASE = "/api/v1/applicability"

FERRY = {
    "vessel_name": "M/F Testfærgen",
    "flag_state": "DK",
    "primary_type": "passenger",
    "gross_tonnage": 3200,
    "max_passengers": 420,
    "operating_area": "international",
    "keel_laid_date": "2010-03-01",
    "assessment_date": "2026-10-20",
}

FIRE_TEXT = (
    "§ 4. Stk. 2. Passagerskibe med en bruttotonnage på 500 og derover skal "
    "være forsynet med et fast brandslukningsanlæg."
)
MANNING_TEXT = (
    "§ 7. Skibe med plads til 10 eller flere besætningsmedlemmer skal have "
    "en bemandingsplan godkendt af Søfartsstyrelsen."
)


def _batch(*drafts: dict) -> DraftBatch:
    return DraftBatch.model_validate({
        "schema_version": 1,
        "generator": {"name": "test-generator", "version": "1"},
        "drafts": list(drafts),
    })


def fire(rule_id: str = "BEK-FIRE-4-2", **overrides) -> dict:
    rule = {
        "rule_id": rule_id,
        "legal_source_id": "BEK-2019-1234",
        "citation": "Bekendtgørelse om brandsikring § 4, stk. 2",
        "topic_category": "fire_protection",
        "source_url": "https://www.retsinformation.dk/eli/lta/2019/1234",
        "inclusion_conditions": [
            {"field": "primary_type", "operator": "eq", "target_value": "passenger"},
            {"field": "gross_tonnage", "operator": "gte", "target_value": 500},
        ],
        **overrides,
    }
    return {"rule": rule, "source_text": FIRE_TEXT, "source_document": {"source_id": "TEST-FIRE"}}


def manning() -> dict:
    return {
        "rule": {
            "rule_id": "BEK-MANNING-7",
            "legal_source_id": "BEK-2019-9999",
            "citation": "Bekendtgørelse om bemanding § 7",
            "topic_category": "manning",
            "inclusion_conditions": [
                {"field": "crew_capacity", "operator": "gte", "target_value": 10}
            ],
        },
        "source_text": MANNING_TEXT,
        "source_document": {"source_id": "TEST-MANNING"},
    }


@pytest.fixture(autouse=True)
def _clean_cache():
    evaluation_cache.invalidate_all()
    yield
    evaluation_cache.invalidate_all()


def _approved(session, *drafts: dict) -> dict[str, str]:
    batch = _batch(*drafts)
    seed_source_documents(session, batch)
    hashes = ingest(session, batch)
    approve(session, hashes)
    return hashes


def _evaluate(client, profile: dict | None = None) -> dict:
    response = client.post(f"{BASE}/evaluate", json=profile or FERRY)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Ingen skrivende HTTP-vej
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", [f"{BASE}/rules/draft", f"{BASE}/rules/BEK-FIRE-4-2/approve"]
)
def test_regler_kan_ikke_aendres_over_http(api_client, path):
    """Heller ikke med administratortoken og ENABLE_ADMIN_API=true."""
    assert api_client.post(path, json={}).status_code in (404, 405)


# ---------------------------------------------------------------------------
# Vurdering
# ---------------------------------------------------------------------------


def test_udkast_indgaar_ikke_i_vurderingen(public_api_client, session):
    batch = _batch(fire())
    seed_source_documents(session, batch)
    ingest(session, batch)
    report = _evaluate(public_api_client)
    assert report["rules_evaluated"] == 0
    assert report["topics"] == []
    assert "IKKE" in report["coverage_notice"]


def test_godkendt_regel_vurderes_og_grupperes_efter_emne(public_api_client, session):
    _approved(session, fire(), manning())

    report = _evaluate(public_api_client)
    assert report["deterministic"] is True
    assert report["used_language_model"] is False
    assert report["rules_evaluated"] == 2
    assert report["counts"]["APPLIES"] == 1
    assert report["counts"]["NEEDS_MANUAL_REVIEW"] == 1

    topics = {t["topic_category"]: t for t in report["topics"]}
    assert list(topics) == ["fire_protection", "manning"]

    result = topics["fire_protection"]["results"][0]
    assert result["verdict"] == "APPLIES"
    assert result["citation"] == "Bekendtgørelse om brandsikring § 4, stk. 2"
    assert result["matched_attributes"] == {"primary_type": "passenger", "gross_tonnage": 3200.0}
    assert result["conditions"] and result["steps"]

    crew = topics["manning"]["results"][0]
    assert crew["verdict"] == "NEEDS_MANUAL_REVIEW"
    assert crew["missing_fields"] == ["crew_capacity"]


def test_rapporten_er_reproducerbar(public_api_client, session):
    _approved(session, fire())
    first = _evaluate(public_api_client)
    evaluation_cache.invalidate_all()
    second = _evaluate(public_api_client)
    assert first["ruleset_fingerprint"] == second["ruleset_fingerprint"]
    assert first["profile_hash"] == second["profile_hash"]
    assert first["topics"] == second["topics"]


def test_ugyldig_profil_afvises(public_api_client):
    response = public_api_client.post(f"{BASE}/evaluate", json={**FERRY, "flag_state": "DNK"})
    assert response.status_code == 422
    response = public_api_client.post(f"{BASE}/evaluate", json={**FERRY, "hull_colour": "red"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_gentaget_vurdering_kommer_fra_cachen(public_api_client, session):
    _approved(session, fire())
    assert _evaluate(public_api_client)["cached"] is False
    assert _evaluate(public_api_client)["cached"] is True


def test_godkendelse_i_en_anden_proces_rammer_ikke_gammel_cache(public_api_client, session):
    """Godkendelser sker i drift-containeren — en anden proces. Fingeraftrykket
    læses fra databasen, så API'ets gamle cachepost bliver uopnåelig."""
    _approved(session, fire())
    before = _evaluate(public_api_client)
    assert _evaluate(public_api_client)["cached"] is True

    batch = _batch(fire("BEK-FIRE-5", citation="Bekendtgørelse om brandsikring § 5"))
    hashes = ingest(session, batch)
    from app.services.vessel_applicability import VesselApplicabilityService
    from app.services.vessel_applicability.cache import EvaluationCache

    # "Den anden proces" har sin egen cache; API'ets bliver ikke ryddet.
    VesselApplicabilityService(session, cache=EvaluationCache()).approve(
        "BEK-FIRE-5",
        reviewer_id="jacob",
        signature="sig",
        expected_content_hash=hashes["BEK-FIRE-5"],
    )
    session.commit()

    after = _evaluate(public_api_client)
    assert after["cached"] is False
    assert after["rules_evaluated"] == 2
    assert after["ruleset_fingerprint"] != before["ruleset_fingerprint"]


# ---------------------------------------------------------------------------
# Integritet
# ---------------------------------------------------------------------------


def _row(session, rule_id: str = "BEK-FIRE-4-2"):
    from app.models import VesselApplicabilityRule

    return session.scalar(
        select(VesselApplicabilityRule).where(VesselApplicabilityRule.rule_id == rule_id)
    )


def test_regel_aendret_efter_godkendelse_afgoeres_ikke(public_api_client, session):
    _approved(session, fire())
    row = _row(session)
    # En direkte rettelse i databasen, uden om gennemgangen.
    row.inclusion_conditions = [
        {"field": "primary_type", "operator": "eq", "target_value": "cargo"}
    ]
    session.commit()

    result = _evaluate(public_api_client)["topics"][0]["results"][0]
    assert result["verdict"] == "NEEDS_MANUAL_REVIEW"
    assert "svarer ikke til det, der blev godkendt" in result["reasons"][0]


def test_regel_uden_kildedokument_afgoeres_ikke(public_api_client, session):
    """Fx en regel godkendt før migration 0009, eller hvis versionen slettes."""
    _approved(session, fire())
    row = _row(session)
    row.document_version_id = None
    session.commit()

    result = _evaluate(public_api_client)["topics"][0]["results"][0]
    assert result["verdict"] == "NEEDS_MANUAL_REVIEW"
    assert "ikke efterprøvet mod et høstet kildedokument" in result["reasons"][0]


def test_ulaeselig_godkendt_regel_forsvinder_ikke(public_api_client, session):
    _approved(session, fire())
    row = _row(session)
    row.inclusion_conditions = [{"felt": "noget helt andet"}]
    session.commit()

    report = _evaluate(public_api_client)
    assert report["rules_evaluated"] == 1
    assert report["topics"][0]["results"][0]["verdict"] == "NEEDS_MANUAL_REVIEW"

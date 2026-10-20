"""Vessel Applicability 2.0 — API, godkendelse og cache.

Den vigtigste påstand: kun godkendte regler, med uændret indhold, indgår i
en vurdering. Resten af filen er detaljerne omkring den grænse.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.services.vessel_applicability import evaluation_cache

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

SOURCE_TEXT = (
    "§ 4. Stk. 2. Passagerskibe med en bruttotonnage på 500 og derover skal "
    "være forsynet med et fast brandslukningsanlæg."
)


def draft_payload(rule_id: str = "BEK-FIRE-4-2", **rule_overrides) -> dict:
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
    }
    rule.update(rule_overrides)
    return {"rule": rule, "source_text": SOURCE_TEXT, "drafted_by": "drafter"}


def approve_payload(**overrides) -> dict:
    return {"reviewer_id": "expert", "signature": "sig:expert:2026-10-20", **overrides}


@pytest.fixture(autouse=True)
def _clean_cache():
    evaluation_cache.invalidate_all()
    yield
    evaluation_cache.invalidate_all()


def _draft(api_client, **kwargs) -> dict:
    response = api_client.post(f"{BASE}/rules/draft", json=draft_payload(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def _approve(api_client, rule_id: str = "BEK-FIRE-4-2", **kwargs):
    return api_client.post(f"{BASE}/rules/{rule_id}/approve", json=approve_payload(**kwargs))


def _evaluate(client, profile: dict | None = None) -> dict:
    response = client.post(f"{BASE}/evaluate", json=profile or FERRY)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Udkast
# ---------------------------------------------------------------------------


def test_udkast_gemmes_som_draft_uanset_indsendt_status(api_client):
    body = _draft(api_client, status="APPROVED")
    assert body["status"] == "DRAFT"
    assert len(body["content_hash"]) == 64
    assert body["source_text_hash"]
    assert [e["event_type"] for e in body["events"]] == ["DRAFTED"]


def test_udkast_indgaar_ikke_i_vurderingen(api_client, public_api_client):
    _draft(api_client)
    report = _evaluate(public_api_client)
    assert report["rules_evaluated"] == 0
    assert report["topics"] == []
    assert "IKKE" in report["coverage_notice"]


def test_udkast_med_ugyldig_betingelse_afvises(api_client):
    payload = draft_payload(
        inclusion_conditions=[{"field": "hull_colour", "operator": "eq", "target_value": "red"}]
    )
    response = api_client.post(f"{BASE}/rules/draft", json=payload)
    assert response.status_code == 422
    assert "hull_colour" in response.json()["detail"]["errors"][0]


def test_udkast_kan_erstattes_saa_laenge_det_er_et_udkast(api_client):
    first = _draft(api_client)
    second = _draft(
        api_client,
        inclusion_conditions=[{"field": "gross_tonnage", "operator": "gte", "target_value": 1000}],
    )
    assert second["content_hash"] != first["content_hash"]
    assert [e["event_type"] for e in second["events"]] == ["DRAFTED", "REDRAFTED"]


def test_godkendt_regel_kan_ikke_overskrives_af_et_udkast(api_client):
    _draft(api_client)
    assert _approve(api_client).status_code == 200
    response = api_client.post(f"{BASE}/rules/draft", json=draft_payload())
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Godkendelse
# ---------------------------------------------------------------------------


def test_godkendelse_registrerer_anmelder_signatur_og_hash(api_client):
    drafted = _draft(api_client)
    response = _approve(api_client, content_hash=drafted["content_hash"])
    assert response.status_code == 200, response.text
    rule = response.json()["rule"]
    assert rule["status"] == "APPROVED"
    assert rule["approved_by"] == "expert"
    assert rule["approved_content_hash"] == drafted["content_hash"]
    assert rule["events"][-1]["event_type"] == "APPROVED"
    assert rule["events"][-1]["previous_status"] == "DRAFT"


def test_godkendelse_af_aendret_udkast_afvises(api_client):
    _draft(api_client)
    response = _approve(api_client, content_hash="0" * 64)
    assert response.status_code == 409
    assert "ændret" in response.json()["detail"]


def test_udkaster_kan_ikke_godkende_egen_regel(api_client):
    _draft(api_client)
    response = _approve(api_client, reviewer_id="Drafter")
    assert response.status_code == 409


def test_kun_udkast_kan_godkendes(api_client):
    _draft(api_client)
    assert _approve(api_client).status_code == 200
    assert _approve(api_client).status_code == 409


def test_ukendt_regel_giver_404(api_client):
    assert _approve(api_client, rule_id="FINDES-IKKE").status_code == 404


def test_godkendelse_kraever_signatur(api_client):
    _draft(api_client)
    response = api_client.post(
        f"{BASE}/rules/BEK-FIRE-4-2/approve", json={"reviewer_id": "expert"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Vurdering
# ---------------------------------------------------------------------------


def test_godkendt_regel_vurderes_og_grupperes_efter_emne(api_client, public_api_client):
    _draft(api_client)
    _draft(
        api_client,
        rule_id="BEK-MANNING-7",
        topic_category="manning",
        citation="Bekendtgørelse om bemanding § 7",
        inclusion_conditions=[{"field": "crew_capacity", "operator": "gte", "target_value": 10}],
    )
    _approve(api_client)
    _approve(api_client, rule_id="BEK-MANNING-7")

    report = _evaluate(public_api_client)
    assert report["deterministic"] is True
    assert report["used_language_model"] is False
    assert report["rules_evaluated"] == 2
    assert report["counts"]["APPLIES"] == 1
    assert report["counts"]["NEEDS_MANUAL_REVIEW"] == 1

    topics = {t["topic_category"]: t for t in report["topics"]}
    assert list(topics) == ["fire_protection", "manning"]

    fire = topics["fire_protection"]["results"][0]
    assert fire["verdict"] == "APPLIES"
    assert fire["citation"] == "Bekendtgørelse om brandsikring § 4, stk. 2"
    assert fire["matched_attributes"] == {"primary_type": "passenger", "gross_tonnage": 3200.0}
    assert fire["conditions"] and fire["steps"]

    manning = topics["manning"]["results"][0]
    assert manning["verdict"] == "NEEDS_MANUAL_REVIEW"
    assert manning["missing_fields"] == ["crew_capacity"]


def test_rapporten_er_reproducerbar(api_client, public_api_client):
    _draft(api_client)
    _approve(api_client)
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


def test_gentaget_vurdering_kommer_fra_cachen(api_client, public_api_client):
    _draft(api_client)
    _approve(api_client)
    assert _evaluate(public_api_client)["cached"] is False
    assert _evaluate(public_api_client)["cached"] is True


def test_godkendelse_ugyldiggoer_cachede_vurderinger(api_client, public_api_client):
    _draft(api_client)
    _approve(api_client)
    before = _evaluate(public_api_client)
    _evaluate(public_api_client)  # nu i cachen

    _draft(api_client, rule_id="BEK-FIRE-5", citation="Bekendtgørelse om brandsikring § 5")
    response = _approve(api_client, rule_id="BEK-FIRE-5")
    assert response.json()["invalidated_evaluations"] >= 1

    after = _evaluate(public_api_client)
    assert after["cached"] is False
    assert after["rules_evaluated"] == 2
    assert after["ruleset_fingerprint"] != before["ruleset_fingerprint"]


def test_ny_regel_i_anden_proces_rammer_ikke_gammel_cache(api_client, public_api_client, session):
    """Fingeraftrykket læses fra databasen: en godkendelse, denne proces
    aldrig så, gør alligevel den gamle post uopnåelig."""
    _draft(api_client)
    _approve(api_client)
    _evaluate(public_api_client)

    _draft(api_client, rule_id="BEK-FIRE-5", citation="Bekendtgørelse om brandsikring § 5")
    from app.services.vessel_applicability import VesselApplicabilityService
    from app.services.vessel_applicability.cache import EvaluationCache

    # "Den anden proces" har sin egen cache; vores bliver ikke ryddet.
    VesselApplicabilityService(session, cache=EvaluationCache()).approve(
        "BEK-FIRE-5", reviewer_id="expert", signature="sig"
    )
    after = _evaluate(public_api_client)
    assert after["cached"] is False
    assert after["rules_evaluated"] == 2


# ---------------------------------------------------------------------------
# Integritet
# ---------------------------------------------------------------------------


def test_regel_aendret_efter_godkendelse_afgoeres_ikke(api_client, public_api_client, session):
    _draft(api_client)
    _approve(api_client)

    from app.models import VesselApplicabilityRule

    row = session.scalar(
        select(VesselApplicabilityRule).where(VesselApplicabilityRule.rule_id == "BEK-FIRE-4-2")
    )
    # En direkte rettelse i databasen, uden om gennemgangen.
    row.inclusion_conditions = [
        {"field": "primary_type", "operator": "eq", "target_value": "cargo"}
    ]
    session.commit()

    result = _evaluate(public_api_client)["topics"][0]["results"][0]
    assert result["verdict"] == "NEEDS_MANUAL_REVIEW"
    assert "svarer ikke til det, der blev godkendt" in result["reasons"][0]


def test_ulaeselig_godkendt_regel_forsvinder_ikke(api_client, public_api_client, session):
    _draft(api_client)
    _approve(api_client)

    from app.models import VesselApplicabilityRule

    row = session.scalar(select(VesselApplicabilityRule))
    row.inclusion_conditions = [{"felt": "noget helt andet"}]
    session.commit()

    report = _evaluate(public_api_client)
    assert report["rules_evaluated"] == 1
    assert report["topics"][0]["results"][0]["verdict"] == "NEEDS_MANUAL_REVIEW"

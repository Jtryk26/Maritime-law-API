"""Regressionstest for adskillelse af flagstat og skibsregister, samt ren læsetilstand."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas.applicability import EvaluationRequest, JurisdictionIn, VesselProfileIn
from app.services.applicability.derive import derive_facts
from app.services.applicability.engine import evaluate_applicability
from app.services.applicability.fields import resolve_field
from app.services.applicability.profile import (
    Dimensions,
    Jurisdiction,
    Measured,
    ValueSource,
    VesselProfile,
    VesselType,
)


def test_jurisdiction_dis_registration():
    """DIS er et dansk skibsregister under dansk flag (DK)."""
    jurisdiction = Jurisdiction(
        flag_state="DK",
        ship_registry="DIS",
        operating_areas=["WORLDWIDE"],
    )
    profile = VesselProfile(
        profile_id="p-dis",
        vessel_type=VesselType.CHEMICAL_TANKER,
        jurisdiction=jurisdiction,
    )
    derived = derive_facts(profile)

    flag_val = resolve_field(profile, derived, "jurisdiction.flag_state")
    assert flag_val.present is True
    assert flag_val.value == "DK"

    reg_val = resolve_field(profile, derived, "jurisdiction.ship_registry")
    assert reg_val.present is True
    assert reg_val.value == "DIS"
    assert reg_val.source == ValueSource.REGISTRY


def test_jurisdiction_das_registration():
    """DAS er det almindelige skibsregister under dansk flag."""
    jurisdiction = Jurisdiction(
        flag_state="DK",
        ship_registry="DAS",
        operating_areas=["DK_TERRITORIAL"],
    )
    profile = VesselProfile(
        profile_id="p-das",
        vessel_type=VesselType.FISHING_VESSEL,
        jurisdiction=jurisdiction,
    )
    derived = derive_facts(profile)

    assert resolve_field(profile, derived, "jurisdiction.flag_state").value == "DK"
    assert resolve_field(profile, derived, "jurisdiction.ship_registry").value == "DAS"


def test_jurisdiction_fas_registration():
    """FAS er det færøske register (FO flag)."""
    jurisdiction = Jurisdiction(
        flag_state="FO",
        ship_registry="FAS",
        operating_areas=["NORTH_SEA"],
    )
    profile = VesselProfile(
        profile_id="p-fas",
        vessel_type=VesselType.GENERAL_CARGO_SHIP,
        jurisdiction=jurisdiction,
    )
    derived = derive_facts(profile)

    assert resolve_field(profile, derived, "jurisdiction.flag_state").value == "FO"
    assert resolve_field(profile, derived, "jurisdiction.ship_registry").value == "FAS"


def test_jurisdiction_rule_condition_evaluation():
    """Regelmotor kan evaluere betingelser baseret på ship_registry."""
    from app.services.applicability.logic import AllOf, Atom, Comparator
    from app.services.applicability.rules import (
        ApplicabilityRuleSpec,
        CoverageLevel,
        ReviewStatus,
        RuleJurisdiction,
        RuleState,
        RuleStatus,
        ScopeCitation,
        ScopeCoverage,
    )

    inclusion_node = AllOf(
        of=[
            Atom(
                id="c1",
                field_name="jurisdiction.flag_state",
                op=Comparator.EQ,
                value="DK",
                citation_key="cit1",
            ),
            Atom(
                id="c2",
                field_name="jurisdiction.ship_registry",
                op=Comparator.EQ,
                value="DIS",
                citation_key="cit2",
            ),
        ]
    )

    rule = ApplicabilityRuleSpec(
        rule_id=901,
        document_id=1,
        rule_ref="§ 1",
        title="DIS-Særlov",
        status=RuleStatus(state=RuleState.IN_FORCE),
        jurisdiction=RuleJurisdiction(flag_states=("DK",)),
        inclusion=inclusion_node,
        coverage=ScopeCoverage(level=CoverageLevel.COMPLETE),
        review_status=ReviewStatus.APPROVED,
        citations={
            "cit1": ScopeCitation(key="cit1", ref="§ 1, stk. 1", text="Gælder for danske skibe"),
            "cit2": ScopeCitation(key="cit2", ref="§ 1, stk. 2", text="Optaget i DIS"),
        },
    )

    dis_profile = VesselProfile(
        profile_id="p-1",
        vessel_type=VesselType.CONTAINER_SHIP,
        jurisdiction=Jurisdiction(flag_state="DK", ship_registry="DIS"),
    )
    res_dis = evaluate_applicability(dis_profile, rule)
    assert res_dis.verdict.value == "APPLIES"

    das_profile = VesselProfile(
        profile_id="p-2",
        vessel_type=VesselType.CONTAINER_SHIP,
        jurisdiction=Jurisdiction(flag_state="DK", ship_registry="DAS"),
    )
    res_das = evaluate_applicability(das_profile, rule)
    assert res_das.verdict.value == "DOES_NOT_APPLY"


def test_schema_backward_compatibility_without_registry():
    """Ældre payloads uden ship_registry behandles korrekt og fejlfrit."""
    payload = EvaluationRequest(
        profile=VesselProfileIn(
            profile_id="legacy-prof",
            vessel_type="passenger_ship",
            jurisdiction=JurisdictionIn(flag_state="DK", operating_areas=["EU"]),
        )
    )
    domain_profile = payload.profile.to_domain()
    assert domain_profile.jurisdiction.flag_state == "DK"
    assert domain_profile.jurisdiction.ship_registry is None

    derived = derive_facts(domain_profile)
    reg_val = resolve_field(domain_profile, derived, "jurisdiction.ship_registry")
    assert reg_val.present is False


def test_production_read_only_app_omits_admin_routes(session):
    """I produktion/læsetilstand (enable_admin_api=False) er admin-ruter 404."""
    read_only_settings = Settings(
        enable_admin_api=False,
        expose_api_docs=False,
        environment="production",
    )
    app = create_app(read_only_settings)
    client = TestClient(app)

    # 1. Offentlige ruter fungerer
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"

    eval_resp = client.post(
        "/api/applicability/evaluate",
        json={
            "profile": {
                "profile_id": "test-1",
                "vessel_type": "fishing_vessel",
                "jurisdiction": {"flag_state": "DK", "ship_registry": "DAS"},
            }
        },
    )
    assert eval_resp.status_code == 200

    # 2. Docs er ikke udstillet
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404

    # 3. Administrative mutationsruter findes slet ikke (404 Not Found)
    assert client.get("/api/admin/session").status_code == 404
    assert client.get("/api/search/queries").status_code == 404
    assert client.post("/api/import/run").status_code == 404
    assert client.get("/api/import/runs").status_code == 404
    assert client.get("/api/stats").status_code == 404
    assert client.post("/api/embeddings/run").status_code == 404
    assert client.post("/api/applicability/drafts/run").status_code == 404
    assert client.get("/api/applicability/review").status_code == 404
    assert client.post("/api/applicability/review/1/decision").status_code == 404



def test_production_invariants_rejections():
    """Produktionsmiljøet afviser opstart hvis usikre konfigurationer er sat."""
    # 1. Afviser ENABLE_ADMIN_API=true i produktion
    with pytest.raises(RuntimeError, match="ENABLE_ADMIN_API=true er strengt forbudt i produktion"):
        create_app(Settings(environment="production", enable_admin_api=True, expose_api_docs=False, run_migrations_on_startup=False))

    # 2. Afviser EXPOSE_API_DOCS=true i produktion
    with pytest.raises(RuntimeError, match="EXPOSE_API_DOCS=true er ikke tilladt i produktion"):
        create_app(Settings(environment="production", enable_admin_api=False, expose_api_docs=True, run_migrations_on_startup=False))

    # 3. Afviser RUN_MIGRATIONS_ON_STARTUP=true i produktion
    with pytest.raises(RuntimeError, match="RUN_MIGRATIONS_ON_STARTUP=true er ikke tilladt i produktion"):
        create_app(Settings(environment="production", enable_admin_api=False, expose_api_docs=False, run_migrations_on_startup=True))

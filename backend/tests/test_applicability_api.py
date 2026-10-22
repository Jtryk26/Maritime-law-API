"""API'et for anvendelighedsvurdering.

Den vigtigste påstand: den offentlige rute ser kun regler, et menneske har
godkendt. Alt andet i denne fil er detaljer omkring den grænse.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from tests.test_applicability_drafting import BEKENDTGOERELSE

FERRY_PROFILE = {
    "profile_id": "prof-ferry",
    "vessel_name": "M/F Testfærgen",
    "vessel_type": "ro_ro_passenger_ship",
    "operation_types": ["international_voyage"],
    "dimensions": {
        "gross_tonnage": {"value": 3200, "source": "certificate"},
        "length_overall_m": {"value": 87.5, "source": "certificate"},
    },
    "persons": {"passenger_count": {"value": 420, "source": "certificate"}},
    "jurisdiction": {"flag_state": "DK", "operating_areas": ["DK_TERRITORIAL", "EU"]},
}


@pytest.fixture()
def stored_document(session):
    """Ét maritimt dokument med rigtig paragrafstruktur, gemt som en import ville."""
    from app.models import Document, DocumentVersion

    document = Document(
        source="test",
        source_id="TEST-PAX-001",
        title="Bekendtgørelse om sikkerhed på passagerskibe",
        display_title="Bekendtgørelse om sikkerhed på passagerskibe",
        authority="Søfartsstyrelsen",
        document_type="Bekendtgørelse",
        status="Gældende",
        published_date=date(2019, 6, 12),
        effective_date=date(2019, 7, 1),
        is_maritime=True,
        maritime_score=90,
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        content=BEKENDTGOERELSE,
        content_hash="1" * 64,
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(version)
    session.flush()
    document.current_version_id = version.id
    session.commit()
    return document


# Udkast og gennemgang findes kun som CLI (`python -m app.cli applicability`),
# kørt i drift-containeren. Testene går samme vej.


def _run_drafts() -> None:
    from app.cli import main

    assert main(["applicability", "draft", "--scope", "maritime"]) == 0


def _queue(review_status: str = "draft") -> list[int]:
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import ApplicabilityRule

    with session_scope() as session:
        return list(session.scalars(
            select(ApplicabilityRule.id)
            .where(ApplicabilityRule.review_status == review_status)
            .order_by(ApplicabilityRule.id)
        ))


def _decide(rule_id: int, decision: str = "approved", *, coverage: str | None = "partial") -> int:
    from app.cli import main

    argv = [
        "applicability", "review", "--rule-id", str(rule_id), "--decision", decision,
        "--actor", "jacob", "--note", "Læst mod kilden.",
    ]
    if coverage:
        argv += ["--coverage", coverage]
    return main(argv)


# ---------------------------------------------------------------------------
# Offentlige ruter
# ---------------------------------------------------------------------------


class TestOffentligeRuter:
    def test_feltregisteret_kan_hentes_uden_token(self, public_api_client):
        """Frontenden skal kunne bygge profilformularen uden en udrulning."""
        response = public_api_client.get("/api/applicability/fields")
        assert response.status_code == 200
        names = {field["name"] for field in response.json()}
        assert "dim.gross_tonnage" in names
        assert "persons.passenger_count" in names
        hint = next(f for f in response.json() if f["name"] == "derived.is_passenger_ship")
        assert hint["input_hint"]

    def test_vurdering_uden_godkendte_regler_giver_et_tomt_men_gyldigt_svar(
        self, public_api_client, stored_document
    ):
        response = public_api_client.post(
            "/api/applicability/evaluate", json={"profile": FERRY_PROFILE}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["rules_evaluated"] == 0
        assert body["results"] == []
        assert "Retsinformation" in body["legal_notice"]
        assert body["engine"]["used_language_model"] is False

    def test_udkast_slaar_ikke_igennem_paa_den_offentlige_rute(
        self, public_api_client, stored_document
    ):
        _run_drafts()
        assert len(_queue()) == 1

        response = public_api_client.post(
            "/api/applicability/evaluate", json={"profile": FERRY_PROFILE}
        )
        assert response.json()["rules_evaluated"] == 0

    def test_godkendt_regel_giver_en_afgoerelse_med_ordret_citat(
        self, public_api_client, stored_document
    ):
        _run_drafts()
        rule_id = _queue()[0]
        assert _decide(rule_id) == 0

        response = public_api_client.post(
            "/api/applicability/evaluate", json={"profile": FERRY_PROFILE}
        )
        body = response.json()
        assert body["rules_evaluated"] == 1
        card = body["results"][0]

        assert card["verdict"] in {"APPLIES", "POSSIBLY_APPLIES"}
        assert card["rule_ref"] == "§ 1"
        assert card["citations"], "afgørelsen skal bære ordret skoptekst"
        assert "finder anvendelse" in card["citations"][0]["text"]
        assert card["decision_path"][0]["gate"] == "temporal_status"
        assert len(card["decision_path"]) == 6
        assert "inputhash" in card["audit_text"]

    def test_delvis_daekning_giver_hoejst_gaelder_muligvis(
        self, public_api_client, stored_document
    ):
        _run_drafts()
        rule_id = _queue()[0]
        _decide(rule_id, coverage="partial")

        card = public_api_client.post(
            "/api/applicability/evaluate", json={"profile": FERRY_PROFILE}
        ).json()["results"][0]
        assert card["verdict"] == "POSSIBLY_APPLIES"
        assert card["coverage_level"] == "partial"
        assert any("modelleret" in warning for warning in card["warnings"])

    def test_manglende_oplysning_bliver_til_et_felt_brugeren_kan_udfylde(
        self, public_api_client, stored_document
    ):
        _run_drafts()
        rule_id = _queue()[0]
        _decide(rule_id)

        # Samme faerge, men uden maalebrevets bruttotonnage. Reglen bider paa
        # 500 BT, saa spoergsmaalet kan ikke afgoeres — og svaret skal sige
        # hvilket felt der mangler i stedet for at gaette.
        profile = {**FERRY_PROFILE, "dimensions": {}}
        body = public_api_client.post(
            "/api/applicability/evaluate", json={"profile": profile}
        ).json()
        card = body["results"][0]
        assert card["verdict"] == "NEEDS_MANUAL_REVIEW"
        assert [item["field"] for item in card["missing_inputs"]] == ["dim.gross_tonnage"]
        assert card["missing_inputs"][0]["unit"] == "BT"

    def test_forkert_skibstype_giver_et_rent_nej_uden_taerskelopslag(
        self, public_api_client, stored_document
    ):
        """Metadata foerst: BT slaas ikke op, naar skibstypen allerede afgoer sagen."""
        _run_drafts()
        _decide(_queue()[0])

        profile = {**FERRY_PROFILE, "vessel_type": "general_cargo_ship"}
        card = public_api_client.post(
            "/api/applicability/evaluate", json={"profile": profile}
        ).json()["results"][0]
        assert card["verdict"] == "DOES_NOT_APPLY"
        thresholds = next(s for s in card["decision_path"] if s["gate"] == "thresholds")
        assert thresholds["outcome"] == "skipped"

    def test_regeldetaljer_er_kun_offentlige_naar_reglen_er_godkendt(
        self, public_api_client, stored_document
    ):
        _run_drafts()
        rule_id = _queue()[0]

        assert public_api_client.get(f"/api/applicability/rules/{rule_id}").status_code == 404
        _decide(rule_id)
        response = public_api_client.get(f"/api/applicability/rules/{rule_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["review_status"] == "approved"
        assert body["citations"]
        assert body["conditions"]

    def test_ugyldig_profil_afvises(self, public_api_client):
        response = public_api_client.post(
            "/api/applicability/evaluate",
            json={"profile": {**FERRY_PROFILE, "vessel_type": "rumskib"}},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Ingen administrative HTTP-ruter
# ---------------------------------------------------------------------------


class TestIngenDriftsruter:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/applicability/drafts/run"),
            ("get", "/api/applicability/drafts/runs"),
            ("get", "/api/applicability/review"),
            ("get", "/api/applicability/review/1"),
            ("post", "/api/applicability/review/1/decision"),
            ("post", "/api/applicability/evaluate/preview"),
        ],
    )
    def test_findes_ikke_heller_ikke_med_administratortoken(self, api_client, method, path):
        """api_client har ADMIN_API_TOKEN og ENABLE_ADMIN_API=true."""
        kwargs = {"json": {}} if method == "post" else {}
        response = getattr(api_client, method)(path, **kwargs)
        assert response.status_code in (404, 405)


# ---------------------------------------------------------------------------
# Gennemgang via CLI
# ---------------------------------------------------------------------------


class TestGennemgang:
    def test_koeen_viser_hvor_meget_der_er_udtrukket(self, capsys, stored_document):
        from app.cli import main

        _run_drafts()
        capsys.readouterr()
        assert main(["applicability", "review"]) == 0
        out = capsys.readouterr().out
        assert "draft" in out
        assert "betingelser" in out and "mangler" in out
        assert "Bekendtgørelse" in out

    def test_complete_kan_ikke_saettes_mens_der_er_aabne_mangler(
        self, capsys, stored_document
    ):
        """Et menneske må ikke kunne godkende en modellering, systemet selv
        har erklæret ufuldstændig."""
        _run_drafts()
        rule_id = _queue()[0]
        capsys.readouterr()
        assert _decide(rule_id, coverage="complete") == 1
        assert "mangler" in capsys.readouterr().out
        assert _queue("approved") == []

    def test_afgoerelsen_skrives_i_revisionssporet(self, public_api_client, stored_document):
        _run_drafts()
        rule_id = _queue()[0]
        _decide(rule_id)

        body = public_api_client.get(f"/api/applicability/rules/{rule_id}").json()
        events = [event["event_type"] for event in body["review_events"]]
        assert "APPROVED" in events and "DRAFTED" in events
        approved = next(e for e in body["review_events"] if e["event_type"] == "APPROVED")
        assert approved["actor"] == "jacob"
        assert approved["previous_status"] == "draft"

    def test_afvist_udkast_forsvinder_fra_koeen(self, stored_document):
        _run_drafts()
        rule_id = _queue()[0]
        assert _decide(rule_id, "rejected", coverage=None) == 0
        assert _queue() == []

    def test_kaerslen_registreres(self, session, stored_document):
        from sqlalchemy import select

        from app.models import ApplicabilityDraftRun

        _run_drafts()
        session.expire_all()
        runs = session.scalars(select(ApplicabilityDraftRun)).all()
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "COMPLETED"
        assert run.scope == "maritime"
        assert run.documents_scanned == 1
        assert run.rules_created == 1

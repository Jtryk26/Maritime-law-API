"""Offline candidate registry checks; no real-world source/licence is asserted."""

import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Document, DocumentVersion
from app.models.international import AcquisitionJob, InstrumentIdentifier, LegalProvenance, LegalSource
from app.services.catalogue.imo import CANDIDATE_SEED, DEFAULT_SEED, CuratedIMOClient, read_seed, seed_imo
from app.services.retsinformation.base import SourceClient
from app.services.rights import RightsDenied


@pytest.fixture()
def catalogue(seeded_session, monkeypatch):
    monkeypatch.setenv("INTERNATIONAL_CATALOGUE_ENABLED", "true")
    get_settings.cache_clear()
    yield seeded_session
    get_settings.cache_clear()


def load_candidates(session):
    return seed_imo(session, path=CANDIDATE_SEED, allow_unverified=True, enable_provider=True)


def seed_file(tmp_path, change):
    payload = yaml.safe_load(CANDIDATE_SEED.read_text())
    change(payload)
    path = tmp_path / "seed.yaml"
    path.write_text(yaml.safe_dump(payload))
    return path


def test_candidates_cover_roadmap_families_without_legal_claims():
    seed = read_seed(CANDIDATE_SEED)
    ids = {r.external_id for r in seed.records}
    assert len(ids) == 37
    assert {"SOLAS", "MARPOL", "STCW", "COLREG", "LOAD-LINES", "SAR", "FAL", "TONNAGE",
            "BWM", "AFS", "HONG-KONG", "CSC", "STCW-F", "CLC", "FUND", "BUNKERS", "LLMC",
            "ATHENS", "NAIROBI", "SALVAGE", "HNS", "ISM", "ISPS", "FSS", "LSA", "FTP", "IS",
            "GRAIN", "IMSBC", "IMDG", "IBC", "IGC", "IGF", "POLAR", "ESP", "HSC", "NOX-TECHNICAL"} == ids
    assert all(r.review_state == "unverified" and r.original_title is None and r.canonical_url is None
               and r.effective_date is None and r.adopted_date is None and r.legal_status is None for r in seed.records)
    client = CuratedIMOClient(seed)
    assert isinstance(client, SourceClient)
    with pytest.raises(RightsDenied):
        client.get_document_text("SOLAS")


def test_unverified_input_is_refused_by_default_before_writes(catalogue):
    with pytest.raises(ValueError, match="verification is pending"):
        seed_imo(catalogue, path=CANDIDATE_SEED, enable_provider=True)
    assert catalogue.scalar(select(func.count()).select_from(LegalSource)) == 0
    assert catalogue.scalar(select(func.count()).select_from(Document)) == 0


def test_provider_and_global_switches_must_both_be_explicit(catalogue, monkeypatch):
    with pytest.raises(RightsDenied):
        seed_imo(catalogue, path=CANDIDATE_SEED, allow_unverified=True)
    catalogue.rollback()
    monkeypatch.setenv("INTERNATIONAL_CATALOGUE_ENABLED", "false")
    get_settings.cache_clear()
    with pytest.raises(RightsDenied):
        load_candidates(catalogue)
    catalogue.rollback()
    assert catalogue.scalar(select(func.count()).select_from(Document)) == 0


def test_seed_is_idempotent_with_provenance_identifiers_and_no_body(catalogue):
    first = load_candidates(catalogue)
    catalogue.commit()
    second = load_candidates(catalogue)
    catalogue.commit()
    assert first.created == 37 and first.failed == 0
    assert second.unchanged == 37 and second.created == second.updated == second.failed == 0
    for model in (Document, DocumentVersion, LegalProvenance, InstrumentIdentifier, AcquisitionJob):
        assert catalogue.scalar(select(func.count()).select_from(model)) == 37
    assert all(not v.content for v in catalogue.scalars(select(DocumentVersion)))
    for provenance in catalogue.scalars(select(LegalProvenance)):
        assert provenance.coverage_status == "official_link_only"
        assert provenance.evidence_links and provenance.retrieved_at and len(provenance.source_hash) == 64
        assert provenance.rights_evidence_id is None


@pytest.mark.parametrize("mutation", [
    lambda p: p["records"].append(dict(p["records"][0])),
    lambda p: p.update(source_url="https://unofficial.example/imo"),
    lambda p: p.update(terms_url="http://www.imo.org/terms"),
    lambda p: p["records"][0].update(effective_date="2000-01-01"),
    lambda p: p["records"][0].update(review_state="verified"),
    lambda p: p["records"][0].update(content="This field must never be accepted"),
])
def test_invalid_duplicate_or_unsupported_seed_is_rejected_before_writes(catalogue, tmp_path, mutation):
    path = seed_file(tmp_path, mutation)
    with pytest.raises(ValueError):
        seed_imo(catalogue, path=path, allow_unverified=True, enable_provider=True)
    assert catalogue.scalar(select(func.count()).select_from(Document)) == 0
    assert catalogue.scalar(select(func.count()).select_from(LegalSource)) == 0


def test_catalogue_routes_are_absent_with_default_feature_flag(seeded_session):
    from app.main import create_app
    with TestClient(create_app()) as client:
        assert client.get("/api/instruments").status_code == 404
        assert client.get("/api/instruments/1").status_code == 404
        assert "/api/instruments" not in client.get("/openapi.json").json()["paths"]


def test_catalogue_metadata_queries_labels_pagination_and_private_body_boundary(catalogue):
    load_candidates(catalogue)
    catalogue.commit()
    from app.main import create_app
    with TestClient(create_app()) as client:
        page = client.get("/api/instruments", params={"page_size": 10}).json()
        assert page["total"] == 37 and page["total_pages"] == 4 and len(page["items"]) == 10
        second = client.get("/api/instruments", params={"page_size": 10, "page": 2}).json()
        assert {r["id"] for r in page["items"]}.isdisjoint({r["id"] for r in second["items"]})
        result = client.get("/api/instruments", params={"q": "solas", "provider_key": "imo"}).json()
        assert result["total"] == 1
        item = result["items"][0]
        assert item["metadata_review_state"] == "unverified" and "Unverified" in item["availability_label"]
        assert item["rights_review_state"] == "unknown" and not item["has_full_text"]
        assert item["original_title"] is None and item["canonical_url"] is None
        assert item["language"] is None and item["adopted_date"] is None and item["legal_status"] is None
        assert item["acquisition_method"] == "curated_offline_seed"
        assert item["evidence_links"] and item["identifiers"] == {"imo_family": "SOLAS"}
        assert client.get("/api/instruments", params={"instrument_type": "code_family"}).json()["total"] == 16
        assert client.get("/api/instruments", params={"availability": "metadata_only"}).json()["total"] == 0
        assert client.get("/api/instruments", params={"provider_key": "retsinformation"}).json()["total"] == 0
        assert client.get("/api/instruments", params={"page_size": 101}).status_code == 422
        # The candidate catalogue must not alter the existing Danish facets,
        # category counts or document listing when no Danish rows were added.
        facets = client.get("/api/facets").json()
        assert facets["authorities"] == facets["document_types"] == facets["statuses"] == []
        assert all(category["document_count"] == 0 for category in facets["categories"])
        assert client.get("/api/documents").json()["total"] == 0
        document = catalogue.get(Document, item["id"])
        # Simulate erroneous private storage; output must still whitelist metadata.
        document.current_version.content = "PRIVATE_TEST_BODY_NO_RIGHTS"
        metadata = dict(document.current_version.metadata_json)
        metadata["source"] = {"body": "PRIVATE_TEST_BODY_NO_RIGHTS"}
        metadata["normalized"] = {**metadata["normalized"], "extra": {
            **metadata["normalized"]["extra"], "review_note": "PRIVATE_TEST_BODY_NO_RIGHTS",
        }}
        document.current_version.metadata_json = metadata
        catalogue.commit()
        response = client.get(f"/api/instruments/{item['id']}")
        assert response.status_code == 200 and "PRIVATE_TEST_BODY" not in response.text
        assert "content" not in response.json() and "snippet" not in response.json()
        assert client.get(f"/api/documents/{item['id']}").status_code == 404
        catalogue.get(LegalSource, "imo").enabled = False
        catalogue.commit()
        assert client.get(f"/api/instruments/{item['id']}").status_code == 404
        assert client.get("/api/instruments").json()["total"] == 0


def test_reviewed_metadata_can_replace_candidate_without_inventing_text_or_licence(catalogue, tmp_path):
    load_candidates(catalogue)
    catalogue.commit()
    def reviewed_fixture(payload):
        record = payload["records"][0]
        record.update(review_state="verified", original_title="SOLAS", canonical_url="https://www.imo.org/fixture/solas",
            reviewed_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), reviewer="fixture-reviewer",
            evidence_links=["https://www.imo.org/fixture/solas"])
        record["verification_evidence"] = [{"url": record["canonical_url"], "observed_at":
            (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), "status": 200,
            "page_sha256": "0" * 64, "matched_title": "SOLAS", "match_kind": "heading"}]
        payload["records"] = [record]
        payload["review_note"] = "Invented review fixture; not a real-world verification."
    path = seed_file(tmp_path, reviewed_fixture)
    summary = seed_imo(catalogue, path=path)
    catalogue.commit()
    assert summary.updated == 1 and summary.created == 0 and summary.failed == 0
    assert catalogue.scalar(select(func.count()).select_from(DocumentVersion)) == 37
    from app.main import create_app
    with TestClient(create_app()) as client:
        item = client.get("/api/instruments", params={"q": "SOLAS"}).json()["items"][0]
        assert item["metadata_review_state"] == "verified" and item["metadata_reviewer"] == "fixture-reviewer"
        assert item["canonical_url"] == "https://www.imo.org/fixture/solas"
        assert item["coverage_status"] == "metadata_only"
        assert item["rights_review_state"] == "unknown" and not item["has_full_text"]


def test_cli_requires_explicit_candidate_opt_in_and_is_repeatable(catalogue, capsys):
    from app.cli import main
    assert main(["catalogue", "seed-imo", "--file", str(CANDIDATE_SEED), "--enable-provider"]) == 1
    assert "verification is pending" in capsys.readouterr().err
    args = ["catalogue", "seed-imo", "--file", str(CANDIDATE_SEED), "--allow-unverified", "--enable-provider"]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert first["created"] == 37 and first["full_text_imported"] is False
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert second["unchanged"] == 37 and second["created"] == 0

"""Deterministic registry/provenance checks; network verification is explicit CLI."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from urllib.request import Request

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Document, DocumentVersion
from app.models.international import LegalProvenance, RightsEvidence
from app.services.catalogue.imo import CANDIDATE_SEED, DEFAULT_SEED, read_seed, seed_imo
from app.services.catalogue.verification import MetadataText, OfficialRedirects, verify_sources


@pytest.fixture()
def enabled_catalogue(seeded_session, monkeypatch):
    monkeypatch.setenv("INTERNATIONAL_CATALOGUE_ENABLED", "true")
    get_settings.cache_clear()
    yield seeded_session
    get_settings.cache_clear()


def test_all_37_candidates_have_official_names_links_and_dated_observations():
    seed = read_seed()
    assert DEFAULT_SEED != CANDIDATE_SEED
    assert {r.external_id for r in seed.records} == {r.external_id for r in read_seed(CANDIDATE_SEED).records}
    assert len(seed.records) == 37
    for record in seed.records:
        assert record.review_state == "verified" and record.original_title and record.canonical_url
        assert record.reviewer and record.reviewed_at and record.verification_evidence
        assert record.language == "en" and record.legal_status is None
        # Instrument-family verification does not establish legal clocks.
        assert record.effective_date is None and record.adopted_date is None
        for proof in record.verification_evidence:
            assert proof.url in record.evidence_links and proof.status == 200
            assert proof.observed_at <= record.reviewed_at and len(proof.page_sha256) == 64
    records = {r.external_id: r for r in seed.records}
    assert "liability" in records["HNS"].original_title.lower()
    assert "oprc-hns" not in records["HNS"].canonical_url
    assert "SHIPS USING GASES" in records["IGF"].original_title.upper()
    assert "MSC" in records["IGF"].verification_evidence[0].url.upper()
    assert "2011" in records["ESP"].original_title
    assert "2008" in records["NOX-TECHNICAL"].original_title


def test_default_verified_seed_is_idempotent_and_does_not_grant_rights(enabled_catalogue):
    session = enabled_catalogue
    first = seed_imo(session, enable_provider=True)
    session.commit()
    second = seed_imo(session)
    session.commit()
    assert first.created == 37 and first.failed == 0
    assert second.unchanged == 37 and second.created == second.updated == second.failed == 0
    assert session.scalar(select(func.count()).select_from(Document)) == 37
    assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 37
    assert session.scalar(select(func.count()).select_from(RightsEvidence)) == 0
    for provenance in session.scalars(select(LegalProvenance)):
        assert provenance.coverage_status == "metadata_only" and provenance.rights_evidence_id is None
    assert all(not version.content for version in session.scalars(select(DocumentVersion)))
    from app.main import create_app
    with TestClient(create_app()) as client:
        page = client.get("/api/instruments", params={"q": "SOLAS"}).json()
        assert page["total"] == 1
        item = page["items"][0]
        assert item["metadata_review_state"] == "verified" and item["verification_evidence"]
        assert item["rights_review_state"] == "unknown" and item["has_full_text"] is False
        assert item["original_title"].startswith("International Convention")
        assert "content" not in item and "snippet" not in item
        assert client.get(f"/api/documents/{item['id']}").status_code == 404


def test_default_cli_no_longer_needs_unverified_override(enabled_catalogue, capsys):
    from app.cli import main
    assert main(["catalogue", "seed-imo", "--enable-provider"]) == 0
    first = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert first["created"] == 37 and first["metadata_verification"] == "reviewed input"
    assert first["full_text_imported"] is False
    assert main(["catalogue", "seed-imo"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["unchanged"] == 37


@pytest.mark.parametrize("change", [
    lambda r: r.update(verification_evidence=[]),
    lambda r: r.update(original_title="Unsubstantiated title"),
    lambda r: r["verification_evidence"][0].update(status=202),
    lambda r: r["verification_evidence"][0].update(page_sha256="not-a-hash"),
    lambda r: r["verification_evidence"][0].update(url="https://unofficial.example/title"),
    lambda r: r["verification_evidence"][0].update(observed_at="2099-01-01T00:00:00+00:00"),
    lambda r: r.update(evidence_links=[]),
])
def test_verified_data_requires_complete_consistent_observations(tmp_path, change):
    payload = yaml.safe_load(DEFAULT_SEED.read_text())
    change(payload["records"][0])
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(payload))
    with pytest.raises(ValueError):
        read_seed(path)


@pytest.mark.parametrize("contents", ["[]", "records: [not-an-object]", "[invalid yaml"])
def test_malformed_registry_is_refused_with_a_useful_error(tmp_path, contents):
    path = tmp_path / "registry.yaml"
    path.write_text(contents)
    with pytest.raises(ValueError):
        read_seed(path)


def recorded_pages(seed):
    pages = {}
    for record in seed.records:
        for proof in record.verification_evidence:
            page = pages.setdefault(proof.url, {"status": 200, "text": "", "page_sha256": proof.page_sha256})
            page["text"] += " " + proof.matched_title
    return pages


def test_live_checker_uses_bounded_deduplicated_metadata_requests():
    seed = read_seed()
    pages = recorded_pages(seed)
    fetch = Mock(side_effect=lambda url: pages[url])
    pause = Mock()
    report = verify_sources(seed, fetch=fetch, pause=pause)
    assert report["checked"] == report["verified"] == 37 and report["failed"] == 0
    assert fetch.call_count == len(pages) == report["unique_pages"]
    assert pause.call_count == len(pages) - 1
    assert report["text_reuse_granted"] is False


def test_access_denial_is_not_reported_as_success_or_a_licence():
    report = verify_sources(read_seed(), fetch=Mock(side_effect=OSError("access denied")), pause=Mock())
    assert report["verified"] == 0 and report["failed"] == report["checked"] == 37
    assert report["page_errors"] and report["text_reuse_granted"] is False


def test_html_changes_need_no_invented_legal_status_and_missing_titles_fail():
    seed = read_seed()
    pages = recorded_pages(seed)
    for page in pages.values():
        page["page_sha256"] = "f" * 64
    changed = verify_sources(seed, fetch=lambda url: pages[url], pause=Mock())
    assert changed["verified"] == 37 and all(r["changed_page_hashes"] for r in changed["results"])
    pages[seed.records[0].verification_evidence[0].url]["text"] = "Unrelated page"
    missing = verify_sources(seed, fetch=lambda url: pages[url], pause=Mock())
    assert missing["failed"] >= 1
    assert missing["results"][0]["verified"] is False
    assert all(record.legal_status is None for record in seed.records)


def test_script_text_and_off_host_redirects_cannot_supply_official_evidence():
    parser = MetadataText()
    parser.feed('<h1>Visible title</h1><script>Hidden title</script><style>Hidden style</style>')
    assert "Visible title" in parser.parts and "Hidden title" not in parser.parts
    with pytest.raises(ValueError, match="official HTTPS"):
        OfficialRedirects().redirect_request(Request("https://www.imo.org/metadata"), None,
            302, "Found", {}, "https://unofficial.example/document")

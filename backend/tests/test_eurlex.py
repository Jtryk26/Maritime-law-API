"""EUR-Lex M3: controlled service responses, never real permissions."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from app.core.config import get_settings
from app.models import Document, DocumentVersion
from app.models.international import LegalProvenance, LegalSource
from app.services.eurlex.client import CellarClient, legal_text
from app.services.eurlex.curated import Selection, read_selection
from app.services.eurlex.ingest import import_eurlex
from app.services.eurlex.transport import CellarTransport
from app.services.retsinformation.base import PermanentSourceError, SourceClient, TransientSourceError
from app.services.rights import RightsDenied, RightsService
from eurlex_support import HTML, cellar_transport, review_file


@pytest.fixture()
def catalogue(seeded_session, monkeypatch):
    monkeypatch.setenv("INTERNATIONAL_CATALOGUE_ENABLED", "true")
    get_settings.cache_clear()
    yield seeded_session
    get_settings.cache_clear()


def ingest(session, **kwargs):
    return import_eurlex(session, enable_provider=True, **kwargs)


def selection_file(tmp_path, *, version="32009L0016", language="en"):
    record = read_selection()[0].model_dump()
    record.update(version_celex=version, language=language)
    path = tmp_path / f"selection-{language}.yaml"
    path.write_text(yaml.safe_dump({"records": [record]}))
    return path


def document(session):
    return session.scalar(select(Document).where(Document.source_id == "32009L0016"))


def test_selection_small_and_explicit():
    records = read_selection()
    assert {r.celex for r in records} == {"32009L0016", "32002L0059", "32015R0757"}
    assert all(r.celex == r.version_celex for r in records)


@pytest.mark.parametrize("change", [
    {"celex": '32009L0016" }'}, {"version_celex": "02002L0059-20250101"},
    {"version_celex": "02009L0016-20250230"}, {"version_celex": "02009L0016"},
    {"eli": "https://unofficial.example/eli/dir/2009/16/oj"},
    {"eli": "https://eur-lex.europa.eu/eli/dir/2009/17/oj"}, {"language": "xx"}])
def test_selection_rejects_invalid_identity(change):
    with pytest.raises(ValueError):
        Selection.model_validate({**read_selection()[0].model_dump(), **change})


def test_metadata_is_repeatable_without_body_requests(catalogue):
    calls = []
    first = ingest(catalogue, transport=cellar_transport(calls))
    assert first.created == 3 and first.failed == 0
    catalogue.commit()
    again = ingest(catalogue, transport=cellar_transport(calls))
    catalogue.commit()
    assert again.unchanged == 3 and not again.updated
    assert len(calls) == 6 and all(r.url.path == "/webapi/rdf/sparql" for r in calls)
    assert catalogue.scalar(select(func.count()).select_from(DocumentVersion)) == 3
    assert all(not v.content for v in catalogue.scalars(select(DocumentVersion)))
    doc = document(catalogue)
    prov = catalogue.get(LegalProvenance, doc.current_version_id)
    assert prov.source_version["celex"] == "32009L0016"
    assert prov.source_hash_scope == "cellar_metadata_response_bytes" and prov.rights_evidence_id is None
    from app.main import create_app
    with TestClient(create_app()) as client:
        item = client.get(f"/api/instruments/{doc.id}").json()
        assert item["identifiers"]["celex"] == "32009L0016" and item["metadata_review_state"] == "verified"
        assert item["version_number"] == 1 and not item["has_full_text"]
        assert client.get(f"/api/instruments/{doc.id}/text").status_code == 403
        assert client.get(f"/api/documents/{doc.id}").status_code == 404


def test_both_feature_flags_required(catalogue, monkeypatch):
    with pytest.raises(RightsDenied):
        import_eurlex(catalogue, transport=cellar_transport())
    catalogue.rollback()
    monkeypatch.setenv("INTERNATIONAL_CATALOGUE_ENABLED", "false")
    get_settings.cache_clear()
    with pytest.raises(RightsDenied):
        ingest(catalogue, transport=cellar_transport())


def test_permitted_content_provenance_display_and_revocation(catalogue, tmp_path):
    calls = []
    result = ingest(catalogue, selection=selection_file(tmp_path), rights_review=review_file(tmp_path), transport=cellar_transport(calls))
    catalogue.commit()
    assert result.created == 1 and result.failed == 0 and len(calls) == 2
    doc = document(catalogue)
    prov = catalogue.get(LegalProvenance, doc.current_version_id)
    assert prov.source_hash == hashlib.sha256(HTML).hexdigest() and prov.rights_evidence_id
    assert RightsService(catalogue).export_text(doc.current_version) == legal_text(HTML)
    structure = doc.current_version.metadata_json["normalized"]["extra"]["legal_structure"]
    assert structure["units"][0]["id"] == "art_1"
    assert prov.rights_context["attribution"] == "Synthetic fixture, not EU law"
    from app.main import create_app
    with TestClient(create_app()) as client:
        assert client.get(f"/api/instruments/{doc.id}").json()["has_full_text"]
        response = client.get(f"/api/instruments/{doc.id}/text")
        assert response.status_code == 200 and response.json()["content"] == legal_text(HTML)
        assert response.json()["attribution"] == "Synthetic fixture, not EU law"
        assert response.headers["cache-control"] == "no-store"
        RightsService(catalogue).revoke_and_purge(prov.rights_evidence_id)
        catalogue.commit()
        assert client.get(f"/api/instruments/{doc.id}/text").status_code == 403
        assert not client.get(f"/api/instruments/{doc.id}").json()["has_full_text"]


@pytest.mark.parametrize("actions,unresolved", [(["store_text"], False), (["fetch_text"], False), (["fetch_text", "store_text"], True)])
def test_incomplete_permissions_do_not_fetch_text(catalogue, tmp_path, actions, unresolved):
    calls = []
    result = ingest(catalogue, selection=selection_file(tmp_path), rights_review=review_file(tmp_path, actions=actions, unresolved=unresolved), transport=cellar_transport(calls))
    assert result.created == 1 and len(calls) == 1 and not document(catalogue).current_version.content


def test_store_permission_does_not_imply_display(catalogue, tmp_path):
    ingest(catalogue, selection=selection_file(tmp_path), rights_review=review_file(tmp_path, actions=["fetch_text", "store_text"]), transport=cellar_transport())
    catalogue.commit()
    doc = document(catalogue)
    assert doc.current_version.content
    from app.main import create_app
    with TestClient(create_app()) as client:
        assert client.get(f"/api/instruments/{doc.id}/text").status_code == 403
        assert not client.get(f"/api/instruments/{doc.id}").json()["has_full_text"]
        assert client.get("/api/search", params={"q": "Synthetic shipping"}).json()["total"] == 0


def test_new_edition_preserves_history_without_reusing_prior_grant(catalogue, tmp_path):
    ingest(catalogue, selection=selection_file(tmp_path), rights_review=review_file(tmp_path), transport=cellar_transport())
    catalogue.commit()
    old_id = document(catalogue).current_version_id
    calls = []
    result = ingest(catalogue, selection=selection_file(tmp_path, version="02009L0016-20250101"), transport=cellar_transport(calls))
    catalogue.commit()
    doc = document(catalogue)
    assert result.updated == 1 and len(calls) == 1 and doc.current_version_id != old_id
    assert doc.current_version.version_number == 2 and not doc.current_version.content
    assert catalogue.get(DocumentVersion, old_id).content == legal_text(HTML)
    assert catalogue.get(LegalProvenance, old_id).source_version["kind"] == "original"
    assert catalogue.get(LegalProvenance, doc.current_version_id).rights_evidence_id is None


def test_empty_metadata_editions_still_have_distinct_versions(catalogue, tmp_path):
    ingest(catalogue, selection=selection_file(tmp_path), transport=cellar_transport())
    catalogue.commit()
    old_id = document(catalogue).current_version_id
    ingest(catalogue, selection=selection_file(tmp_path, version="02009L0016-20250101"), transport=cellar_transport())
    catalogue.commit()
    assert document(catalogue).current_version_id != old_id
    assert catalogue.get(LegalProvenance, old_id).source_version["kind"] == "original"


def test_language_change_retains_prior_expression(catalogue, tmp_path):
    ingest(catalogue, selection=selection_file(tmp_path), transport=cellar_transport())
    ingest(catalogue, selection=selection_file(tmp_path, language="da"), transport=cellar_transport())
    assert catalogue.scalar(select(func.count()).select_from(Document)) == 1
    assert catalogue.scalar(select(func.count()).select_from(DocumentVersion)) == 2
    assert document(catalogue).current_version.version_number == 2


@pytest.mark.parametrize("mutate", [lambda p: p["results"].update(bindings=[]), lambda p: p["results"]["bindings"][0]["celex"].update(value="WRONG"), lambda p: p["results"]["bindings"][0]["work"].update(value="https://unofficial.example/work")])
def test_bad_metadata_cannot_create_documents(catalogue, mutate):
    result = ingest(catalogue, transport=cellar_transport(mutate=mutate))
    assert result.failed == 3 and not result.created
    assert catalogue.scalar(select(func.count()).select_from(Document)) == 0


@pytest.mark.parametrize("status", [202, 401, 403])
def test_challenges_are_terminal_without_fallback(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"x-amzn-waf-action": "challenge"})
    transport = CellarTransport(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PermanentSourceError, match="denied/challenged"):
        transport.get("https://publications.europa.eu/", accept="application/xhtml+xml")
    assert len(calls) == 1


@pytest.mark.parametrize("destination", ["https://evil.example/", "https://eur-lex.europa.eu/", "http://publications.europa.eu/", "https://user:pass@publications.europa.eu/"])
def test_redirects_cannot_escape_cellar(destination):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": destination})
    transport = CellarTransport(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PermanentSourceError, match="approved HTTPS"):
        transport.get("https://publications.europa.eu/", accept="application/xhtml+xml")
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_server_failure_is_deferred(status):
    transport = CellarTransport(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))))
    with pytest.raises(TransientSourceError):
        transport.get("https://publications.europa.eu/", accept="application/json")


@pytest.mark.parametrize("body", [b'<html><body>AWSWAF challenge</body></html>', b'<html><body>captcha</body></html>', b'<html><body>empty</body></html>', b'Not HTML'])
def test_challenge_or_empty_body_is_not_text(body):
    with pytest.raises(PermanentSourceError):
        legal_text(body)


def test_expired_review_does_not_write(catalogue, tmp_path):
    path = review_file(tmp_path)
    payload = json.loads(path.read_text())
    payload[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        ingest(catalogue, rights_review=path, transport=cellar_transport())
    assert catalogue.scalar(select(func.count()).select_from(LegalSource)) == 0


def test_source_protocol(catalogue):
    ingest(catalogue, transport=cellar_transport())
    client = CellarClient(read_selection(), RightsService(catalogue), transport=cellar_transport())
    assert isinstance(client, SourceClient)
    with pytest.raises(PermanentSourceError, match="change feed"):
        client.get_updated_documents(None)


def test_re_review_binds_new_version_without_rewriting_prior_evidence(catalogue, tmp_path):
    selection = selection_file(tmp_path)
    review = review_file(tmp_path)
    ingest(catalogue, selection=selection, rights_review=review, transport=cellar_transport())
    catalogue.commit()
    old_id = document(catalogue).current_version_id
    old_provenance = catalogue.get(LegalProvenance, old_id)
    old_evidence = old_provenance.rights_evidence_id
    payload = json.loads(review.read_text())
    payload[0]["reviewer"] = "Second synthetic test reviewer"
    payload[0]["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    review.write_text(json.dumps(payload))
    result = ingest(catalogue, selection=selection, rights_review=review, transport=cellar_transport())
    catalogue.commit()
    doc = document(catalogue)
    assert result.updated == 1 and doc.current_version_id != old_id
    assert catalogue.get(LegalProvenance, old_id).rights_evidence_id == old_evidence
    assert catalogue.get(LegalProvenance, doc.current_version_id).rights_evidence_id != old_evidence
    assert catalogue.get(DocumentVersion, old_id).content == doc.current_version.content


@pytest.mark.parametrize("celex,language", [("32002L0059", "en"), ("32009L0016", "da")])
def test_wrong_manifestation_identity_or_language_is_refused(celex, language):
    with pytest.raises(PermanentSourceError, match="could not be verified"):
        legal_text(HTML, celex=celex, language=language)


def test_missing_permissions_fail_before_any_network_request(catalogue):
    ingest(catalogue, transport=cellar_transport())
    calls = []
    client = CellarClient(read_selection(), RightsService(catalogue), transport=cellar_transport(calls))
    with pytest.raises(RightsDenied):
        client.get_document("32009L0016")
    assert not calls


def test_import_cli_reports_per_document_errors(catalogue, monkeypatch, capsys):
    from app.cli import main
    import app.services.eurlex.ingest as ingestion
    original = ingestion.CellarClient
    monkeypatch.setattr(ingestion, "CellarClient", lambda records, rights, **kw:
        original(records, rights, transport=cellar_transport(mutate=lambda p: p["results"].update(bindings=[]))))
    assert main(["catalogue", "import-eurlex", "--enable-provider"]) == 1
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["documents_failed"] == 3 and result["status"] == "COMPLETED_WITH_ERRORS"



def test_cellar_canonical_http_resource_is_upgraded_before_io():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(303, headers={"location": "http://publications.europa.eu/resource/cellar/uuid.0006.03/DOC_1"})
        return httpx.Response(200, content=HTML, headers={"content-type": "application/xhtml+xml"})
    transport = CellarTransport(httpx.Client(transport=httpx.MockTransport(handler)))
    body, final, _ = transport.get("https://publications.europa.eu/resource/celex/32009L0016", accept="application/xhtml+xml")
    assert body == HTML and final.startswith("https://publications.europa.eu/resource/cellar/")
    assert len(calls) == 2 and all(request.url.scheme == "https" for request in calls)
    assert calls[0].headers["accept-language"] == "eng"


def test_formex_is_explicitly_unsupported():
    from app.services.eurlex.xhtml import parse_xhtml
    with pytest.raises(PermanentSourceError, match="Formex"):
        parse_xhtml(b"<ACT><TITLE>Formex needs its own parser</TITLE></ACT>")


def test_structure_does_not_survive_a_denied_store_permission(catalogue, tmp_path):
    from dataclasses import replace
    from app.services.eurlex.xhtml import parse_xhtml
    ingest(catalogue, transport=cellar_transport())
    client = CellarClient(read_selection(), RightsService(catalogue), transport=cellar_transport())
    doc = client.get_document_metadata("32009L0016")
    parsed = parse_xhtml(HTML)
    doc = replace(doc, content=parsed.content, metadata={**doc.metadata, "legal_structure": parsed.structure})
    safe = RightsService(catalogue).prepare(doc)
    assert not safe.content and "legal_structure" not in safe.metadata

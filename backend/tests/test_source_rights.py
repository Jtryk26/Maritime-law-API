"""Rights regression fixtures contain invented text, never licensed source text."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.db.migrations_runner import alembic_config
from app.models import Document, DocumentChunk, DocumentVersion
from app.models.international import InstrumentIdentifier, LegalProvenance, RightsEvidence
from app.services.categorization import KeywordCategorizationEngine
from app.services.embedding import EmbeddingIndexer, HashingEmbeddingProvider
from app.services.importer import DocumentRepository, ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation.base import DocumentRef, NormalizedDocument
from app.services.rights import Action, RightsDenied, RightsService
from tests.conftest import make_document

NOW = datetime.now(timezone.utc) - timedelta(seconds=2)
SECRET = "RESTRICTED_BODY_NEVER_PUBLIC"


@pytest.fixture()
def managed(seeded_session, monkeypatch):
    monkeypatch.setenv("INTERNATIONAL_CATALOGUE_ENABLED", "true")
    get_settings.cache_clear()
    rights = RightsService(seeded_session)
    source = rights.register_source("imo", "IMO", ["www.imo.org"], terms_url="https://www.imo.org/terms")
    source.enabled = True
    seeded_session.commit()
    yield seeded_session, rights, source
    get_settings.cache_clear()


def incoming(source_id="SOLAS"):
    return NormalizedDocument(
        source="imo", source_id=source_id, rights_managed=True,
        title="SOLAS — sikkerhed på skibe", authority="IMO", document_type="Convention",
        source_url="https://www.imo.org/en/About/Conventions/Pages/SOLAS.aspx",
        content=(SECRET + " § 1. Dette er kun opfundet testtekst til skibssikkerhed. ") * 8,
        metadata={"language": "en", "jurisdiction": "international", "identifiers": {"imo": source_id},
                  "evidence_links": ["https://www.imo.org/en/About/Conventions/Pages/SOLAS.aspx"],
                  "provider_html": SECRET, "review_note": SECRET}, raw_metadata={"body": SECRET},
    )


def store(session, doc):
    return DocumentRepository(session).store(doc,
        KeywordRelevanceEngine().classify(doc), KeywordCategorizationEngine().categorize(doc))


def grant(session, *, actions=(), source_id="SOLAS", evidence_id="review-1", **overrides):
    values = dict(id=evidence_id, provider_key="imo", external_id=source_id,
                  state="open_reuse_verified", allowed_actions=[a.value for a in actions],
                  evidence_url="https://www.imo.org/permissions/example-fixture", reviewer="test-reviewer",
                  reviewed_at=NOW, restrictions={})
    values.update(overrides)
    evidence = RightsEvidence(**values)
    session.add(evidence)
    session.flush()
    return evidence


@pytest.mark.parametrize("state", ["unknown", "metadata_only", "permission_review", "invented"])
@pytest.mark.parametrize("action", list(Action))
def test_unverified_state_denies_every_text_action(state, action):
    evidence = RightsEvidence(state=state, allowed_actions=[a.value for a in Action],
        reviewer="someone", reviewed_at=NOW, evidence_url="https://example.org/terms")
    assert not RightsService.permits(evidence, action)


def test_status_does_not_grant_an_action():
    evidence = RightsEvidence(state="open_reuse_verified", allowed_actions=[],
        reviewer="someone", reviewed_at=NOW, evidence_url="https://example.org/terms")
    assert all(not RightsService.permits(evidence, a) for a in Action)


@pytest.mark.parametrize("field,value", [
    ("reviewer", None), ("reviewed_at", None), ("evidence_url", "not-evidence"),
    ("expires_at", NOW), ("revoked_at", NOW),
    ("restrictions", {"unresolved_conditions": ["written permission"]}),
    ("restrictions", {"attribution_required": True}),
])
def test_incomplete_expired_revoked_or_conditional_evidence_denied(managed, field, value):
    session, rights, _ = managed
    evidence = grant(session, actions=[Action.STORE_TEXT], **{field: value})
    assert not rights.permits(evidence, Action.STORE_TEXT)


def test_expiry_is_exclusive_and_review_cannot_be_in_future(managed):
    session, rights, _ = managed
    expiry = NOW + timedelta(days=1)
    evidence = grant(session, actions=[Action.STORE_TEXT], expires_at=expiry)
    assert rights.permits(evidence, Action.STORE_TEXT, at=expiry - timedelta(microseconds=1))
    assert not rights.permits(evidence, Action.STORE_TEXT, at=expiry)
    assert not rights.permits(evidence, Action.STORE_TEXT, at=NOW - timedelta(seconds=1))


def test_unknown_permissions_remove_body_raw_payload_and_index_text(managed):
    session, rights, _ = managed
    outcome = store(session, incoming())
    session.commit()
    version = outcome.document.current_version
    assert version.content == ""
    assert SECRET not in str(version.metadata_json)
    assert SECRET.lower() not in outcome.document.search_text.lower()
    provenance = session.get(LegalProvenance, version.id)
    assert provenance.coverage_status == "metadata_only"
    assert provenance.language == "en" and provenance.jurisdiction == "international"
    assert provenance.source_hash and provenance.evidence_links and provenance.retrieved_at
    assert provenance.rights_evidence_id is None
    with pytest.raises(RightsDenied):
        rights.export_text(version)


def test_storage_right_does_not_grant_index_display_export_or_embedding(managed):
    session, rights, _ = managed
    grant(session, actions=[Action.FETCH_TEXT, Action.STORE_TEXT])
    outcome = store(session, incoming())
    session.commit()
    version = outcome.document.current_version
    assert SECRET in version.content
    assert SECRET.lower() not in outcome.document.search_text.lower()
    assert not rights.version_permits(version, Action.DISPLAY)
    with pytest.raises(RightsDenied):
        rights.export_text(version)
    provider = Mock(wraps=HashingEmbeddingProvider(dimensions=64))
    written, _ = EmbeddingIndexer(session, provider).index_document(outcome.document)
    assert written == 0
    provider.embed_passages.assert_not_called()
    assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 0


def test_disabled_flags_deny_before_provider_discovery(managed, monkeypatch):
    session, _, source = managed
    client = Mock(kind="imo")
    importer = ImportService(session, client=client, relevance_engine=KeywordRelevanceEngine(),
                             categorization_engine=KeywordCategorizationEngine())
    source.enabled = False
    with pytest.raises(RightsDenied):
        importer.run()
    client.get_documents.assert_not_called()
    source.enabled = True
    monkeypatch.setenv("INTERNATIONAL_CATALOGUE_ENABLED", "false")
    get_settings.cache_clear()
    with pytest.raises(RightsDenied):
        importer.run()
    client.get_documents.assert_not_called()


def test_unknown_rights_use_metadata_method_never_fulltext_fetch(managed):
    session, _, _ = managed
    client = Mock(kind="imo")
    client.get_document_metadata.return_value = incoming()
    importer = ImportService(session, client=client, relevance_engine=KeywordRelevanceEngine(),
                             categorization_engine=KeywordCategorizationEngine())
    result = importer._fetch(DocumentRef(source_id="SOLAS"))
    assert result.content == "" and not result.raw_metadata
    client.get_document.assert_not_called()
    client.get_document_metadata.assert_called_once_with("SOLAS")


def test_registered_source_cannot_forget_managed_flag_and_unknown_managed_source_is_denied(managed):
    session, _, _ = managed
    result = store(session, replace(incoming(), rights_managed=False))
    assert result.document.current_version.content == ""
    with pytest.raises(RightsDenied):
        store(session, replace(incoming(), source="unknown-provider"))


@pytest.mark.parametrize("url", ["http://www.imo.org/text", "https://evil.example/text",
    "https://www.imo.org.evil.example/text", "https://user@www.imo.org/text", "https://www.imo.org:444/text"])
def test_invalid_or_unofficial_source_url_rejected(managed, url):
    session, _, _ = managed
    with pytest.raises(ValueError):
        store(session, replace(incoming(), source_url=url))


def test_duplicate_identifier_rejected_and_repeat_is_idempotent(managed):
    session, _, _ = managed
    first = store(session, incoming())
    second = store(session, incoming())
    assert first.created and second.unchanged
    assert session.scalar(select(func.count()).select_from(InstrumentIdentifier)) == 1
    assert session.scalar(select(func.count()).select_from(LegalProvenance)) == 1
    with pytest.raises(ValueError, match="already belongs"):
        store(session, replace(incoming("OTHER"), metadata={"identifiers": {"imo": "SOLAS"}}))
    session.rollback()


def test_current_public_surfaces_cannot_leak_even_poisoned_stored_body(managed):
    session, _, _ = managed
    grant(session, actions=[Action.FETCH_TEXT, Action.STORE_TEXT, Action.INDEX_TEXT])
    document = store(session, incoming()).document
    session.commit()
    document_id = document.id
    from app.main import create_app
    with TestClient(create_app()) as client:
        for mode in ["lexical", "semantic", "hybrid"]:
            # The API echoes the user's query; search a prefix to distinguish
            # that from disclosure of the stored source body's complete marker.
            response = client.get("/api/search", params={"q": "RESTRICTED", "mode": mode})
            assert response.status_code == 200
            assert SECRET not in response.text
            assert response.json()["total"] == 0
        for suffix in ["", "/versions", "/versions/1", "/structure", "/similar"]:
            response = client.get(f"/api/documents/{document_id}{suffix}")
            assert response.status_code == 404
            assert SECRET not in response.text
        response = client.get("/api/documents")
        assert response.json()["total"] == 0


def test_version_scoped_revocation_preserves_unrelated_versions_and_legacy(managed):
    session, rights, _ = managed
    first_evidence = grant(session, actions=list(Action))
    first = store(session, incoming()).document
    session.commit()
    version1 = first.current_version
    assert rights.export_text(version1)
    EmbeddingIndexer(session, HashingEmbeddingProvider(dimensions=64)).index_document(first)
    session.commit()
    assert session.scalar(select(func.count()).select_from(DocumentChunk)) > 0
    from app.services.search.vector import VectorSearchBackend
    assert not VectorSearchBackend(HashingEmbeddingProvider(dimensions=64)).has_vectors(session)
    grant(session, actions=list(Action), evidence_id="review-2", reviewed_at=NOW + timedelta(seconds=1))
    second = store(session, replace(incoming(), content=incoming().content + "Another invented version."))
    legacy = store(session, make_document("Sølov", "Legacy body remains unchanged."))
    session.commit()
    assert second.version_number == 2
    assert rights.revoke_and_purge(first_evidence.id) == 1
    session.commit()
    assert version1.content == ""
    assert SECRET in first.current_version.content
    assert rights.export_text(first.current_version)
    assert first.embedded_version_id is None and first.chunk_count == 0
    assert legacy.document.current_version.content == "Legacy body remains unchanged."
    assert session.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.version_id == version1.id)) == 0
    with pytest.raises(RightsDenied):
        rights.export_text(version1)


def test_migration_roundtrip_preserves_legacy_rows(seeded_session):
    session = seeded_session
    result = store(session, make_document("Sølov", "Unchanged legacy content."))
    session.commit()
    document_id = result.document.id
    config = alembic_config()
    command.downgrade(config, "0009_vessel_rules_source_document")
    command.upgrade(config, "head")
    assert session.execute(text("SELECT content FROM document_versions WHERE document_id=:id"), {"id": document_id}).scalar_one() == "Unchanged legacy content."
    assert session.execute(text("SELECT count(*) FROM legal_sources")).scalar_one() == 0


def test_rollback_refuses_to_remove_gate_with_managed_documents(managed):
    session, _, _ = managed
    store(session, incoming())
    session.commit()
    with pytest.raises(RuntimeError, match="Remove managed source documents"):
        command.downgrade(alembic_config(), "0009_vessel_rules_source_document")


def test_legacy_source_cannot_be_reclassified_as_international(seeded_session):
    rights = RightsService(seeded_session)
    with pytest.raises(ValueError, match="reserved"):
        rights.register_source("retsinformation", "Wrong", ["www.imo.org"])


def test_existing_legacy_identity_cannot_be_registered_retroactively(seeded_session):
    store(seeded_session, make_document("Sølov"))
    with pytest.raises(ValueError, match="reclassified"):
        RightsService(seeded_session).register_source("test", "Wrong", ["www.imo.org"])


def test_restricted_sources_do_not_generate_applicability_quotes(managed):
    session, _, _ = managed
    grant(session, actions=[Action.FETCH_TEXT, Action.STORE_TEXT])
    document = store(session, incoming()).document
    session.commit()
    from app.services.applicability.service import ApplicabilityService
    from app.services.vessel_applicability.service import InvalidRule, SourceRef, VesselApplicabilityService
    result = ApplicabilityService(session).run_draft_generation(scope="all", document_ids=[document.id])
    assert result.documents_scanned == 0 and result.rules_created == 0
    with pytest.raises(InvalidRule, match="reviewed integration"):
        VesselApplicabilityService(session).resolve_source(SourceRef(source="imo", source_id="SOLAS"))


def test_latest_main_exact_reference_search_keeps_private_sources_out(managed):
    session, _, _ = managed
    grant(session, actions=list(Action))
    private = store(session, replace(incoming(), document_number="2207")).document
    public = store(session, replace(make_document("Bekendtgørelse om skibssikkerhed", "§ 1. Offentlig testregel."),
                                   document_number="2207")).document
    session.commit()
    from app.main import create_app
    with TestClient(create_app()) as client:
        # The exact-reference path added on latest main must share the rights
        # boundary used by ordinary lexical/vector candidates.
        response = client.get("/api/search", params={"q": "2207", "mode": "lexical"})
        assert response.status_code == 200
        items = response.json()["items"]
        assert items and items[0]["id"] == public.id and items[0]["exact_match"] is True
        assert all(item["id"] != private.id for item in items)
        assert SECRET not in response.text

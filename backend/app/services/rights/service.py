"""Fail-closed, action-specific rights; access never implies a reuse licence.

The existing Danish source path is intentionally left as-is. Registered sources
are gated even if their adapter forgets rights_managed. They are excluded from
legacy public endpoints until a rights-aware catalogue presents their metadata.
"""

import enum
import hashlib
import json
import re
from dataclasses import replace
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.text import content_hash
from app.models import Document, DocumentChunk, DocumentVersion
from app.models.international import InstrumentIdentifier, LegalProvenance, LegalSource, RightsEvidence
from app.services.retsinformation.base import NormalizedDocument


class Action(str, enum.Enum):
    FETCH_TEXT = "fetch_text"
    STORE_TEXT = "store_text"
    INDEX_TEXT = "index_text"
    EMBED = "embeddings"
    DISPLAY = "display_text"
    SNIPPET = "snippets"
    CACHE = "cache_text"
    EXPORT = "export_text"
    SUMMARIZE = "summarize"


class RightsDenied(PermissionError):
    pass


def legacy_documents():
    """SQL boundary shared by lexical/vector search and existing public APIs."""
    return Document.source.not_in(select(LegalSource.key))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class RightsService:
    def __init__(self, session: Session):
        self.session = session

    def source(self, key: str) -> LegalSource | None:
        return self.session.get(LegalSource, key)

    def register_source(self, key: str, name: str, hosts: list[str], *, terms_url: str | None = None) -> LegalSource:
        if (not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", key)
                or key in {"retsinformation", "retsinformation-fixture", "production", "fixture"}):
            raise ValueError("Invalid or reserved provider key")
        if not hosts or any(not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}", host) for host in hosts):
            raise ValueError("Explicit official DNS hostnames are required")
        existing = self.source(key)
        if existing:
            if existing.official_hosts != hosts or existing.name != name or existing.terms_url != terms_url:
                raise ValueError("Source identity changed; review it explicitly")
            return existing
        if self.session.scalar(select(Document.id).where(Document.source == key).limit(1)) is not None:
            raise ValueError("Existing legacy source identities cannot be reclassified")
        source = LegalSource(key=key, name=name, official_hosts=hosts, terms_url=terms_url, enabled=False)
        self.session.add(source)
        self.session.flush()
        return source

    def require_source(self, key: str) -> LegalSource:
        source = self.source(key)
        if not source or not source.enabled or not get_settings().international_catalogue_enabled:
            raise RightsDenied("International source is unregistered or disabled")
        return source

    @staticmethod
    def official_url(source: LegalSource, url: str | None) -> str:
        parsed = urlsplit(url or "")
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.hostname not in source.official_hosts):
            raise ValueError("An official HTTPS source URL is required")
        return url

    def evidence(self, key: str, external_id: str) -> RightsEvidence | None:
        # A newer revoked/closed review supersedes old permissions; no fallback.
        return self.session.scalars(select(RightsEvidence).where(
            RightsEvidence.provider_key == key,
            RightsEvidence.external_id == external_id,
        ).order_by(RightsEvidence.reviewed_at.desc(), RightsEvidence.id.desc())).first()

    @staticmethod
    def permits(evidence: RightsEvidence | None, action: Action, *, at: datetime | None = None) -> bool:
        now = at or datetime.now(timezone.utc)
        if evidence is None or evidence.state not in {"licensed_internal", "open_reuse_verified", "permission_with_conditions"}:
            return False
        if not evidence.reviewer or not evidence.reviewer.strip() or not evidence.reviewed_at or not evidence.evidence_url:
            return False
        parsed = urlsplit(evidence.evidence_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        if _utc(evidence.reviewed_at) > _utc(now) or evidence.revoked_at is not None:
            return False
        if evidence.expires_at is not None and _utc(evidence.expires_at) <= _utc(now):
            return False
        if action.value not in (evidence.allowed_actions or []):
            return False
        # Conditions must be explicitly resolved, not interpreted by this service.
        if (evidence.restrictions or {}).get("unresolved_conditions"):
            return False
        if (evidence.restrictions or {}).get("attribution_required") and not evidence.attribution:
            return False
        return True

    def document_evidence(self, doc: NormalizedDocument) -> RightsEvidence | None:
        evidence = self.evidence(doc.source, doc.source_id)
        scoped_version = (evidence.restrictions or {}).get("version_celex") if evidence else None
        if scoped_version is not None and (scoped_version != doc.metadata.get("source_version", {}).get("celex")
                or (evidence.restrictions or {}).get("language") != doc.metadata.get("language")):
            return None
        return evidence

    def prepare(self, doc: NormalizedDocument) -> NormalizedDocument:
        source = self.source(doc.source)
        if source is None and not doc.rights_managed:
            return doc
        source = self.require_source(doc.source)
        self.official_url(source, doc.source_url)
        evidence = self.document_evidence(doc)
        can_store = self.permits(evidence, Action.FETCH_TEXT) and self.permits(evidence, Action.STORE_TEXT)
        # New-source metadata is a whitelist. Never preserve raw bodies, snippets
        # or arbitrary provider fields under a metadata-only permission.
        safe = {key: doc.metadata[key] for key in (
            "language", "jurisdiction", "adopted_date", "identifiers", "evidence_links", "source_hash",
            "metadata_review_state", "metadata_reviewed_at", "metadata_reviewer", "coverage_status",
            "acquisition_method", "verification_evidence", "source_version", "retrieval_url", "source_hash_scope", "cellar_work_uri"
        ) if key in doc.metadata}
        if can_store and "legal_structure" in doc.metadata:
            safe["legal_structure"] = doc.metadata["legal_structure"]
        return replace(doc, content=doc.content if can_store else "", metadata=safe,
                       raw_metadata={}, rights_managed=True)

    def record(self, doc: NormalizedDocument, outcome) -> None:
        if not doc.rights_managed:
            return
        version = self.session.get(DocumentVersion, outcome.document.current_version_id)
        existing = self.session.get(LegalProvenance, version.id)
        evidence = self.document_evidence(doc)
        # An unchanged version retains its exact historical evidence binding.
        if existing is None:
            raw_hash = doc.metadata.get("source_hash")
            if not isinstance(raw_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_hash):
                raw_hash = hashlib.sha256(json.dumps({"title": doc.title, "metadata": doc.metadata},
                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            adopted = doc.metadata.get("adopted_date")
            self.session.add(LegalProvenance(
                version_id=version.id, provider_key=doc.source, external_id=doc.source_id,
                source_url=doc.source_url, source_hash=raw_hash,
                source_version=doc.metadata.get("source_version", {}),
                rights_context={"state": evidence.state, "attribution": evidence.attribution,
                    "evidence_url": evidence.evidence_url, "restrictions": evidence.restrictions,
                    "authenticity_disclaimer": (evidence.restrictions or {}).get("authenticity_disclaimer"),
                    "licence": (evidence.restrictions or {}).get("licence"),
                    "licence_url": (evidence.restrictions or {}).get("licence_url"),
                    "allowed_actions": evidence.allowed_actions} if evidence else {},
                retrieval_url=doc.metadata.get("retrieval_url"),
                source_hash_scope=doc.metadata.get("source_hash_scope", "normalized_metadata"),
                language=doc.metadata.get("language"), jurisdiction=doc.metadata.get("jurisdiction"),
                adopted_date=date.fromisoformat(adopted) if adopted else None,
                retrieved_at=doc.retrieved_at or datetime.now(timezone.utc),
                evidence_links=doc.metadata.get("evidence_links", [doc.source_url]),
                coverage_status="full_text_permitted" if self.permits(evidence, Action.DISPLAY) and doc.content else
                    ("official_link_only" if doc.metadata.get("coverage_status") == "official_link_only" else "metadata_only"),
                rights_evidence_id=evidence.id if evidence else None,
            ))
        elif outcome.metadata_changed:
            # The existing importer updates metadata in place for a content-
            # unchanged version. Keep its provenance in sync, preserving the
            # exact licence evidence binding rather than inferring a new grant.
            existing.source_url = doc.source_url
            raw_hash = doc.metadata.get("source_hash")
            existing.source_hash = raw_hash if isinstance(raw_hash, str) and re.fullmatch(r"[0-9a-f]{64}", raw_hash) else hashlib.sha256(
                json.dumps({"title": doc.title, "metadata": doc.metadata}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            existing.source_hash_scope = doc.metadata.get("source_hash_scope", "normalized_metadata")
            existing.retrieval_url = doc.metadata.get("retrieval_url")
            existing.language = doc.metadata.get("language")
            existing.jurisdiction = doc.metadata.get("jurisdiction")
            adopted = doc.metadata.get("adopted_date")
            existing.adopted_date = date.fromisoformat(adopted) if adopted else None
            existing.evidence_links = doc.metadata.get("evidence_links", [doc.source_url])
            existing.retrieved_at = doc.retrieved_at or datetime.now(timezone.utc)
            if not doc.content:
                existing.coverage_status = "official_link_only" if doc.metadata.get("coverage_status") == "official_link_only" else "metadata_only"
        for scheme, value in doc.metadata.get("identifiers", {}).items():
            found = self.session.scalars(select(InstrumentIdentifier).where(
                InstrumentIdentifier.provider_key == doc.source,
                InstrumentIdentifier.scheme == scheme, InstrumentIdentifier.value == value,
            )).first()
            if found and found.document_id != outcome.document.id:
                raise ValueError("Instrument identifier already belongs to another document")
            if not found:
                self.session.add(InstrumentIdentifier(document_id=outcome.document.id,
                    provider_key=doc.source, scheme=scheme, value=value))
        self.session.flush()
        self.session.expire(outcome.document, ["current_version", "versions"])

    def version_permits(self, version: DocumentVersion, action: Action) -> bool:
        provenance = self.session.get(LegalProvenance, version.id)
        if not provenance:
            return self.source(version.document.source) is None
        source = self.source(provenance.provider_key)
        if not source or not source.enabled or not get_settings().international_catalogue_enabled:
            return False
        evidence = self.session.get(RightsEvidence, provenance.rights_evidence_id) if provenance.rights_evidence_id else None
        return self.permits(evidence, action)

    def export_text(self, version: DocumentVersion) -> str:
        if not self.version_permits(version, Action.EXPORT):
            raise RightsDenied("Text export is not authorized")
        return version.content

    def revoke_and_purge(self, evidence_id: str) -> int:
        """Explicit takedown, scoped to versions bound to this evidence record.

        Caller owns the transaction. Historical hashes/provenance stay auditable;
        text removal is an exceptional rights action, not ordinary versioning.
        """
        evidence = self.session.get(RightsEvidence, evidence_id)
        if evidence is None:
            raise ValueError("Unknown evidence record")
        evidence.revoked_at = datetime.now(timezone.utc)
        provenances = self.session.scalars(select(LegalProvenance).where(
            LegalProvenance.rights_evidence_id == evidence_id)).all()
        for provenance in provenances:
            version = self.session.get(DocumentVersion, provenance.version_id)
            version.content = ""
            version.content_hash = content_hash("")
            version.metadata_json = {"normalized": {"title": version.document.title}, "source": {}}
            provenance.coverage_status = "rights_review"
            self.session.execute(delete(DocumentChunk).where(DocumentChunk.version_id == version.id))
            document = version.document
            if document.embedded_version_id == version.id:
                document.embedded_version_id = None
                document.embedding_model = None
                document.embedded_at = None
                document.chunk_count = 0
            if document.current_version_id == version.id:
                document.search_text = ""
                document.relevance_details = None
                document.embedded_version_id = None
                document.embedding_model = None
                document.embedded_at = None
                document.chunk_count = 0
                if self.session.get_bind().dialect.name == "postgresql":
                    from sqlalchemy import text
                    self.session.execute(text("UPDATE documents SET search_vector = NULL WHERE id = :id"), {"id": document.id})
        self.session.flush()
        return len(provenances)

"""Opt-in ingestion and explicit, immutable per-expression permissions review."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models.international import AcquisitionJob, RightsEvidence
from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.rights import Action, RightsService

from .client import CellarClient
from .curated import DEFAULT_SELECTION, read_selection
from .transport import CellarTransport

TERMS_URL = "https://eur-lex.europa.eu/content/legal-notice/legal-notice.html"
HOSTS = ["eur-lex.europa.eu", "publications.europa.eu", "op.europa.eu"]


class PermissionReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    external_id: str = Field(pattern=r"^3[0-9]{4}[RL][0-9]{4}$")
    language: Literal["en", "da"] = "en"
    version_celex: str = Field(pattern=r"^[03][0-9]{4}[RL][0-9]{4}(?:-[0-9]{8})?$")
    state: Literal["unknown", "open_reuse_verified", "permission_with_conditions"]
    allowed_actions: list[Action] = Field(default_factory=list)
    evidence_url: str
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=256)
    reviewed_at: datetime
    expires_at: datetime
    attribution: str = Field(min_length=1)
    unresolved_conditions: bool = True
    legal_basis: str | None = Field(default=None, max_length=2000)
    conditions: list[str] = Field(default_factory=list)
    scope_note: str | None = Field(default=None, max_length=4000)
    licence: Literal["CC-BY-4.0"] | None = None
    licence_url: Literal["https://creativecommons.org/licenses/by/4.0/"] | None = None
    authenticity_disclaimer: str | None = Field(default=None, max_length=1000)
    review_basis: Literal["operator_review", "user_approved_policy"] = "operator_review"
    policy_review_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    official_notice_live_verified: bool = False
    supporting_references: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_review(self):
        if not self.reviewer.strip() or not self.attribution.strip():
            raise ValueError("Explicit reviewer and attribution are required")
        if (self.reviewed_at.tzinfo is None or self.expires_at.tzinfo is None
                or not self.reviewed_at <= datetime.now(timezone.utc) < self.expires_at):
            raise ValueError("Review must be dated, current, timezone-aware and have an expiry")
        if self.review_basis == "user_approved_policy" and (
                self.licence != "CC-BY-4.0" or not self.licence_url or not self.policy_review_sha256
                or self.authenticity_disclaimer != "Only European Union legislation published in the Official Journal of the European Union is deemed authentic."):
            raise ValueError("User-approved EU policy requires CC BY 4.0, its source review hash and the exact authenticity disclaimer")
        if self.state == "unknown" and self.allowed_actions:
            raise ValueError("Unknown permissions cannot grant actions")
        if self.state != "unknown" and not self.allowed_actions:
            raise ValueError("A permission review must name its permitted actions")
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("Duplicate permission action")
        return self


def import_eurlex(session: Session, *, selection: Path = DEFAULT_SELECTION,
                  rights_review: Path | None = None, enable_provider: bool = False,
                  transport: CellarTransport | None = None):
    records = read_selection(selection)
    reviews = []
    if rights_review:
        payload = json.loads(rights_review.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Rights review must be a JSON array")
        reviews = [PermissionReview.model_validate(item) for item in payload]
        versions = {record.source_id: (record.version_celex, record.language) for record in records}
        if (len({r.external_id for r in reviews}) != len(reviews)
                or any(versions.get(r.external_id) != (r.version_celex, r.language) for r in reviews)):
            raise ValueError("Review must match a unique selected act, language and exact version")
    rights = RightsService(session)
    source = rights.register_source("eurlex", "EUR-Lex / Publications Office Cellar", HOSTS, terms_url=TERMS_URL)
    if enable_provider:
        source.enabled = True
    rights.require_source("eurlex")
    for review in reviews:
        rights.official_url(source, review.evidence_url)
        payload = review.model_dump(mode="json")
        review_id = "eurlex-" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if not session.get(RightsEvidence, review_id):
            session.add(RightsEvidence(id=review_id, provider_key="eurlex", external_id=review.external_id,
                state=review.state, allowed_actions=[a.value for a in review.allowed_actions],
                evidence_url=review.evidence_url, reviewer=review.reviewer, reviewed_at=review.reviewed_at,
                expires_at=review.expires_at, attribution=review.attribution,
                restrictions={"attribution_required": True, "unresolved_conditions": review.unresolved_conditions,
                    "evidence_sha256": review.evidence_sha256, "version_celex": review.version_celex, "language": review.language,
                    "legal_basis": review.legal_basis, "conditions": review.conditions, "scope_note": review.scope_note,
                    "licence": review.licence, "licence_url": review.licence_url,
                    "authenticity_disclaimer": review.authenticity_disclaimer, "review_basis": review.review_basis,
                    "policy_review_sha256": review.policy_review_sha256,
                    "official_notice_live_verified": review.official_notice_live_verified,
                    "supporting_references": review.supporting_references}))
    session.flush()
    # A review is version scoped. Never let an earlier grant authorize a new
    # consolidated text merely because it has the same underlying act ID.
    client = CellarClient(records, rights, transport=transport)
    try:
        summary = ImportService(session, client=client, relevance_engine=KeywordRelevanceEngine(),
            categorization_engine=KeywordCategorizationEngine(), store_min_score=0).run(trigger="eurlex_curated")
    finally:
        client.close()
    for outcome in summary.outcomes:
        session.add(AcquisitionJob(provider_key="eurlex", external_id=outcome.source_id,
            status=outcome.outcome.value, completed_at=datetime.now(timezone.utc), error_code=outcome.error_type))
    session.flush()
    return summary

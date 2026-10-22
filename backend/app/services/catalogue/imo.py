"""An explicit offline candidate workflow; no purported live IMO integration."""

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT
from app.models.international import AcquisitionJob, LegalSource
from app.schemas.catalogue import MetadataVerification
from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation.base import DocumentNotFoundError, DocumentRef, NormalizedDocument, PermanentSourceError
from app.services.rights import RightsDenied, RightsService

CANDIDATE_SEED = REPO_ROOT / "data" / "fixtures" / "imo_registry_candidates.yaml"
DEFAULT_SEED = REPO_ROOT / "data" / "fixtures" / "imo_registry.yaml"


class CandidateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{0,63}$")
    title: str = Field(min_length=1, max_length=512)
    instrument_type: Literal["convention_family", "liability_family", "code_family"]
    original_title: str | None = Field(default=None, min_length=1, max_length=1000)
    canonical_url: str | None = None
    language: str | None = None
    adopted_date: date | None = None
    published_date: date | None = None
    effective_date: date | None = None
    legal_status: str | None = None
    review_state: Literal["unverified", "verified"] = "unverified"
    reviewed_at: datetime | None = None
    reviewer: str | None = None
    evidence_links: list[str] = Field(default_factory=list)
    verification_evidence: list[MetadataVerification] = Field(default_factory=list)

    @model_validator(mode="after")
    def verified_requires_evidence(self):
        if self.review_state == "verified":
            if not all((self.original_title, self.canonical_url, self.reviewed_at, self.reviewer,
                        self.evidence_links, self.verification_evidence)):
                raise ValueError("Verified metadata needs official title/link, dated review, reviewer and citations")
            reviewed = self.reviewed_at
            if reviewed.tzinfo is None:
                reviewed = reviewed.replace(tzinfo=timezone.utc)
            if reviewed > datetime.now(timezone.utc):
                raise ValueError("Metadata review cannot be in the future")
            normalize = lambda value: " ".join(value.casefold().split())
            if not any(normalize(proof.matched_title) == normalize(self.original_title) for proof in self.verification_evidence):
                raise ValueError("Official title must be supported by a recorded metadata observation")
            if self.canonical_url not in self.evidence_links:
                raise ValueError("Canonical instrument URL must be cited")
            for proof in self.verification_evidence:
                if proof.url not in self.evidence_links:
                    raise ValueError("Every metadata observation must be cited")
                if proof.observed_at.tzinfo is None or proof.observed_at > reviewed:
                    raise ValueError("Metadata observation needs a timezone and cannot follow its review")
        # An unverified candidate must not assert legal dates, status or a title
        # as source-verified merely because it appeared in the roadmap.
        elif any((self.original_title, self.canonical_url, self.adopted_date,
                  self.published_date, self.effective_date, self.legal_status, self.reviewed_at, self.reviewer)):
            raise ValueError("Unverified candidates cannot assert verified titles, links, dates or status")
        return self


class SeedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: Literal["imo"]
    source_url: str
    terms_url: str
    review_state: Literal["unverified", "verified"]
    review_note: str
    records: list[CandidateRecord] = Field(min_length=1, max_length=100)


def read_seed(path: Path = DEFAULT_SEED) -> SeedFile:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("Invalid registry YAML") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Registry input must contain a records list")
    if not all(isinstance(record, dict) for record in payload["records"]):
        raise ValueError("Every registry record must be an object")
    # File-level review is a default, never an automatic verified claim.
    for record in payload.get("records", []):
        record.setdefault("review_state", payload.get("review_state", "unverified"))
    seed = SeedFile.model_validate(payload)
    identifiers = [r.external_id for r in seed.records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate instrument family identifiers in seed")
    source = LegalSource(key="imo", name="IMO", official_hosts=["www.imo.org"])
    for url in [seed.source_url, seed.terms_url] + [u for r in seed.records for u in [r.canonical_url, *r.evidence_links,
            *(proof.url for proof in r.verification_evidence)] if u]:
        RightsService.official_url(source, url)
    return seed


class CuratedIMOClient:
    """SourceClient for reviewed metadata or explicitly requested candidates.

    No network operation and no copyrighted book body is implemented here.
    """

    kind = "imo"

    def __init__(self, seed: SeedFile):
        self.seed = seed
        self.records = {r.external_id: r for r in seed.records}

    def get_documents(self, *, since: date | None = None, explicit_ids: Iterable[str] | None = None):
        if since is not None:
            raise PermanentSourceError("The curated seed has no live change feed")
        ids = list(explicit_ids) if explicit_ids is not None else list(self.records)
        for external_id in ids:
            if external_id not in self.records:
                raise DocumentNotFoundError(external_id)
            yield DocumentRef(source_id=external_id, title=self.records[external_id].title)

    def get_updated_documents(self, since: date):
        return self.get_documents(since=since)

    def get_document_metadata(self, document_id: str) -> NormalizedDocument:
        record = self.records.get(document_id)
        if record is None:
            raise DocumentNotFoundError(document_id)
        values = record.model_dump(mode="json")
        return NormalizedDocument(
            source="imo", source_id=record.external_id,
            title=record.original_title or record.title, document_type=record.instrument_type,
            authority="IMO", source_url=record.canonical_url or self.seed.source_url,
            published_date=record.published_date, effective_date=record.effective_date,
            status=record.legal_status, rights_managed=True,
            metadata={
                "language": record.language, "jurisdiction": "international",
                "adopted_date": values["adopted_date"], "identifiers": {"imo_family": record.external_id},
                "evidence_links": record.evidence_links or [self.seed.source_url],
                "metadata_review_state": record.review_state,
                "metadata_reviewed_at": values["reviewed_at"], "metadata_reviewer": record.reviewer,
                "verification_evidence": values["verification_evidence"],
                "review_note": self.seed.review_note,
                "acquisition_method": "curated_offline_seed",
                "coverage_status": "official_link_only" if record.review_state == "unverified" else "metadata_only",
            },
        )

    def get_document(self, document_id: str) -> NormalizedDocument:
        return self.get_document_metadata(document_id)

    def get_document_text(self, document_id: str) -> str:
        if document_id not in self.records:
            raise DocumentNotFoundError(document_id)
        raise RightsDenied("This provider supplies metadata only; no IMO book text is available")

    def close(self) -> None:
        pass


def seed_imo(session: Session, *, path: Path = DEFAULT_SEED,
             allow_unverified: bool = False, enable_provider: bool = False):
    seed = read_seed(path)  # Validate the complete input before database writes.
    if not allow_unverified and any(r.review_state != "verified" for r in seed.records):
        raise ValueError("Official metadata verification is pending; use --allow-unverified only for isolated candidate development")
    rights = RightsService(session)
    source = rights.register_source("imo", "International Maritime Organization", ["www.imo.org"], terms_url=seed.terms_url)
    if enable_provider:
        source.enabled = True
    rights.require_source("imo")
    session.flush()
    client = CuratedIMOClient(seed)
    summary = ImportService(session, client=client, relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(), store_min_score=0).run(trigger="catalogue_seed")
    # This is an audit record, not a scheduled update or external acquisition.
    for outcome in summary.outcomes:
        if outcome.outcome.value in {"CREATED", "UPDATED"}:
            session.add(AcquisitionJob(provider_key="imo", external_id=outcome.source_id,
                status="METADATA_ONLY", completed_at=datetime.now(timezone.utc)))
    session.flush()
    return summary

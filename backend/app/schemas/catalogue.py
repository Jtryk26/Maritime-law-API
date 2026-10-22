"""Metadata-only output; no body/snippet/cache payload is part of this schema."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class MetadataVerification(BaseModel):
    """Dated observation of an official HTML title, never a text-reuse grant."""

    url: str = Field(max_length=2000)
    observed_at: datetime
    status: Literal[200]
    page_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    matched_title: str = Field(min_length=1, max_length=1000)
    match_kind: Literal["heading", "metadata_text"]


class InstrumentOut(BaseModel):
    id: int
    provider_key: str
    external_id: str
    title: str
    original_title: str | None
    instrument_type: str | None
    language: str | None
    jurisdiction: str | None
    adopted_date: date | None
    published_date: date | None
    effective_date: date | None
    legal_status: str | None
    official_source_url: str
    canonical_url: str | None
    identifiers: dict[str, str]
    metadata_review_state: Literal["verified", "unverified"]
    metadata_reviewed_at: datetime | None
    metadata_reviewer: str | None
    verification_evidence: list[MetadataVerification] = Field(default_factory=list)
    rights_review_state: str
    coverage_status: str
    availability_label: str
    has_full_text: bool = False
    evidence_links: list[str]
    retrieved_at: datetime
    source_hash: str
    source_hash_scope: str
    acquisition_method: str
    review_note: str | None

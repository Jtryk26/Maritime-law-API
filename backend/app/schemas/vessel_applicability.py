"""Udgående skemaer for Vessel Applicability 2.0.

Profil, regel og rapport er domænets egne Pydantic-modeller og genbruges
direkte (`app.services.vessel_applicability.models`). Her ligger visningen
af en gemt regel (``vessel-rules show --json``) og den offentlige
integritetsstatus (``GET /rules/approved``).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.models import VesselApplicabilityRule
from app.services.vessel_applicability import EvaluationCondition, RuleStatus
from app.services.vessel_applicability.service import source_binding

__all__ = ["ApprovedRuleStatusOut", "RuleEventOut", "SourceDocumentOut", "StoredRuleOut"]


class RuleEventOut(BaseModel):
    event_type: str
    actor: str | None
    content_hash: str
    previous_status: str | None
    note: str | None = None
    created_at: datetime


class SourceDocumentOut(BaseModel):
    source: str
    source_id: str
    version_number: int
    content_hash: str


class StoredRuleOut(BaseModel):
    """En gemt regel med hele sit spor — det ``vessel-rules show --json`` skriver."""

    rule_id: str
    status: RuleStatus
    legal_source_id: str
    citation: str
    topic_category: str
    source_url: str | None
    inclusion_conditions: list[EvaluationCondition]
    exclusion_conditions: list[EvaluationCondition]
    valid_from: date | None
    valid_to: date | None
    keel_laid_after: date | None
    keel_laid_before: date | None
    source_text: str | None
    source_text_hash: str | None
    source_document: SourceDocumentOut | None
    content_hash: str
    drafted_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    approved_content_hash: str | None
    approval_signature: str | None
    events: list[RuleEventOut]

    @classmethod
    def from_row(cls, row: VesselApplicabilityRule) -> StoredRuleOut:
        return cls(
            rule_id=row.rule_id,
            status=RuleStatus(row.status),
            legal_source_id=row.legal_source_id,
            citation=row.citation,
            topic_category=row.topic_category,
            source_url=row.source_url,
            inclusion_conditions=row.inclusion_conditions,
            exclusion_conditions=row.exclusion_conditions or [],
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            keel_laid_after=row.keel_laid_after,
            keel_laid_before=row.keel_laid_before,
            source_text=row.source_text,
            source_text_hash=row.source_text_hash,
            source_document=(
                SourceDocumentOut(**source_binding(row.document_version))
                if row.document_version is not None
                else None
            ),
            content_hash=row.content_hash,
            drafted_by=row.drafted_by,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            approved_content_hash=row.approved_content_hash,
            approval_signature=row.approval_signature,
            events=[
                RuleEventOut(
                    event_type=event.event_type,
                    actor=event.actor,
                    content_hash=event.content_hash,
                    previous_status=event.previous_status,
                    note=event.note,
                    created_at=event.created_at,
                )
                for event in row.events
            ],
        )


class ApprovedRuleStatusOut(BaseModel):
    """Én godkendt regel og om den stadig kan bruges.

    ``integrity_verified`` genberegnes ved hvert kald fra rækkens nuværende
    indhold — både betingelser og lovtekst. ``false`` betyder, at reglen
    giver ``NEEDS_MANUAL_REVIEW`` i stedet for en afgørelse.
    """

    rule_id: str
    status: RuleStatus
    citation: str
    legal_source_id: str
    topic_category: str
    approved_by: str
    approved_at: datetime
    approved_content_hash: str
    source_text_hash: str | None
    integrity_verified: bool
    integrity_problem: str | None

"""API-skemaer for Vessel Applicability 2.0.

Profil, regel og rapport er domænets egne Pydantic-modeller og genbruges
direkte (`app.services.vessel_applicability.models`). Her ligger kun det,
der er særegent for HTTP: udkastets indpakning, godkendelsen og visningen
af en gemt regel.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import VesselApplicabilityRule
from app.services.vessel_applicability import (
    ApplicabilityRule,
    EvaluationCondition,
    RuleStatus,
)

__all__ = [
    "ApproveRuleIn",
    "ApprovedRuleOut",
    "ApprovedRuleStatusOut",
    "DraftRuleIn",
    "RuleEventOut",
    "StoredRuleOut",
]


class DraftRuleIn(BaseModel):
    """En skrabet bestemmelse og den regel, den er omsat til.

    ``source_text`` gemmes ordret og indgår (som hash) i reglens
    indholdshash. Anmelderen godkender altså reglen *sammen med* den tekst,
    den er læst ud af.
    """

    model_config = ConfigDict(extra="forbid")

    rule: ApplicabilityRule
    source_text: str = Field(min_length=1, max_length=200_000)
    drafted_by: str | None = Field(default=None, min_length=1, max_length=128)


class ApproveRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=1, max_length=4096)
    #: Den hash, anmelderen har gennemgået (fra udkastets svar). Udfyldes den,
    #: afvises godkendelsen, hvis udkastet er ændret i mellemtiden.
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class RuleEventOut(BaseModel):
    event_type: str
    actor: str | None
    content_hash: str
    previous_status: str | None
    created_at: datetime


class StoredRuleOut(BaseModel):
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
    source_text_hash: str | None
    content_hash: str
    drafted_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    approved_content_hash: str | None
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
            source_text_hash=row.source_text_hash,
            content_hash=row.content_hash,
            drafted_by=row.drafted_by,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            approved_content_hash=row.approved_content_hash,
            events=[
                RuleEventOut(
                    event_type=event.event_type,
                    actor=event.actor,
                    content_hash=event.content_hash,
                    previous_status=event.previous_status,
                    created_at=event.created_at,
                )
                for event in row.events
            ],
        )


class ApprovedRuleOut(BaseModel):
    rule: StoredRuleOut
    #: Cachede vurderinger, denne proces kasserede. Andre processer ser det
    #: nye regelsæt ved næste forespørgsel via fingeraftrykket.
    invalidated_evaluations: int


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

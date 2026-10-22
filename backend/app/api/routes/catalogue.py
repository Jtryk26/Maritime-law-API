"""Opt-in catalogue reads. Existing Danish API schemas and routes are unchanged."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, Pagination
from app.models import Document
from app.models.international import InstrumentIdentifier, LegalProvenance, LegalSource, RightsEvidence
from app.schemas import Page
from app.schemas.catalogue import InstrumentOut

router = APIRouter(tags=["instrument catalogue"])


def _query():
    return (select(Document, LegalProvenance)
        .join(LegalSource, LegalSource.key == Document.source)
        .join(LegalProvenance, LegalProvenance.version_id == Document.current_version_id)
        .where(LegalSource.enabled.is_(True))
        .options(selectinload(Document.current_version)))


def _item(session, document: Document, provenance: LegalProvenance) -> InstrumentOut:
    # Deliberately whitelist scalar metadata; never serialize metadata_json,
    # raw provider payloads, private body, search_text or document chunks.
    extra = ((document.current_version.metadata_json or {}).get("normalized") or {}).get("extra") or {}
    verified = extra.get("metadata_review_state") == "verified"
    evidence = session.get(RightsEvidence, provenance.rights_evidence_id) if provenance.rights_evidence_id else None
    identifiers = session.scalars(select(InstrumentIdentifier).where(InstrumentIdentifier.document_id == document.id)).all()
    return InstrumentOut(
        id=document.id, provider_key=provenance.provider_key, external_id=provenance.external_id,
        title=document.title, original_title=document.title if verified else None,
        instrument_type=document.document_type, language=provenance.language, jurisdiction=provenance.jurisdiction,
        adopted_date=provenance.adopted_date, published_date=document.published_date,
        effective_date=document.effective_date, legal_status=document.status,
        official_source_url=provenance.source_url, canonical_url=provenance.source_url if verified else None,
        identifiers={row.scheme: row.value for row in identifiers},
        metadata_review_state="verified" if verified else "unverified",
        metadata_reviewed_at=extra.get("metadata_reviewed_at"), metadata_reviewer=extra.get("metadata_reviewer"),
        rights_review_state=evidence.state if evidence else "unknown", coverage_status=provenance.coverage_status,
        availability_label="Verified metadata; legal text not supplied" if verified else
            "Unverified candidate; official source starting point only",
        evidence_links=provenance.evidence_links, retrieved_at=provenance.retrieved_at,
        source_hash=provenance.source_hash, source_hash_scope="normalized_metadata",
        acquisition_method=extra.get("acquisition_method", "unknown"),
        review_note="Metadata review recorded; text reuse rights are separate." if verified else
            "Official title, instrument link and dates still require source verification.",
    )


@router.get("/instruments", response_model=Page[InstrumentOut])
def instruments(
    session: DbSession, pagination: Pagination,
    q: Annotated[str | None, Query(max_length=200)] = None,
    provider_key: Annotated[str | None, Query(max_length=64)] = None,
    instrument_type: Annotated[str | None, Query(max_length=128)] = None,
    availability: Annotated[str | None, Query(max_length=40)] = None,
) -> Page[InstrumentOut]:
    page, page_size = pagination
    stmt = _query()
    if q and q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(Document.title.ilike(pattern, escape="\\"), Document.source_id.ilike(pattern, escape="\\")))
    if provider_key:
        stmt = stmt.where(LegalProvenance.provider_key == provider_key)
    if instrument_type:
        stmt = stmt.where(Document.document_type == instrument_type)
    if availability:
        stmt = stmt.where(LegalProvenance.coverage_status == availability)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(stmt.order_by(Document.title, Document.id).offset((page - 1) * page_size).limit(page_size)).all()
    return Page[InstrumentOut](items=[_item(session, document, provenance) for document, provenance in rows],
        total=total, page=page, page_size=page_size, total_pages=(total + page_size - 1) // page_size)


@router.get("/instruments/{instrument_id}", response_model=InstrumentOut)
def instrument(session: DbSession, instrument_id: Annotated[int, Path(ge=1)]) -> InstrumentOut:
    row = session.execute(_query().where(Document.id == instrument_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return _item(session, *row)

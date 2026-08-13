"""Oversættelse fra ORM-modeller til API-skemaer.

Samlet ét sted, så et dokument ser ens ud i søgeresultater, lister og
detaljevisning — og så forklaringen på den maritime klassifikation
altid bygges på samme måde.
"""

from __future__ import annotations

from typing import Any

from app.models import Document, DocumentVersion, ImportRun
from app.schemas import (
    SYNTHETIC_DATA_NOTICE,
    ChangeLogEntryOut,
    DocumentCategoryOut,
    DocumentDetailOut,
    DocumentSummaryOut,
    ImportRunOut,
    RelevanceCalculationOut,
    RelevanceExplanationOut,
    RelevanceTermOut,
    SearchHitOut,
    VersionSummaryOut,
)

__all__ = [
    "document_summary",
    "document_detail",
    "search_hit",
    "relevance_explanation",
    "version_summary",
    "import_run",
]


def _categories(document: Document) -> list[DocumentCategoryOut]:
    links = [link for link in document.category_links if link.category is not None]
    links.sort(key=lambda link: link.confidence, reverse=True)
    return [
        DocumentCategoryOut(
            slug=link.category.slug,
            name=link.category.name,
            confidence=round(link.confidence, 3),
            matched_terms=list(link.matched_terms or []),
        )
        for link in links
    ]


def _classification(document: Document) -> str:
    details = document.relevance_details or {}
    return str(details.get("classification", "not_maritime"))


def _summary_fields(document: Document) -> dict[str, Any]:
    versions = document.versions or []
    current = document.current_version
    return {
        "id": document.id,
        "source_id": document.source_id,
        "retsinformation_id": document.retsinformation_id,
        "document_number": document.document_number,
        "title": document.title,
        "short_title": document.short_title,
        "document_type": document.document_type,
        "authority": document.authority,
        "published_date": document.published_date,
        "effective_date": document.effective_date,
        "status": document.status,
        "is_maritime": document.is_maritime,
        "maritime_score": document.maritime_score,
        "classification": _classification(document),
        "categories": _categories(document),
        "source_url": document.source_url,
        "is_synthetic": document.is_synthetic,
        "current_version_number": current.version_number if current else None,
        "version_count": len(versions),
        "last_retrieved_at": document.last_retrieved_at,
    }


def document_summary(document: Document) -> DocumentSummaryOut:
    return DocumentSummaryOut(**_summary_fields(document))


def search_hit(document: Document, *, rank: float, snippet: str) -> SearchHitOut:
    return SearchHitOut(**_summary_fields(document), rank=round(rank, 4), snippet=snippet)


def version_summary(version: DocumentVersion, *, current_id: int | None) -> VersionSummaryOut:
    return VersionSummaryOut(
        id=version.id,
        version_number=version.version_number,
        content_hash=version.content_hash,
        content_length=len(version.content or ""),
        retrieved_at=version.retrieved_at,
        created_at=version.created_at,
        is_current=version.id == current_id,
    )


def relevance_explanation(document: Document) -> RelevanceExplanationOut:
    """Bygger den fulde forklaring på dokumentets klassifikation.

    Markerer eksplicit hvis vurderingen blev foretaget på en ældre
    version end den aktuelle — så er forklaringen forældet, og det skal
    en bruger kunne se frem for at gætte.
    """
    details = document.relevance_details or {}
    calculation = RelevanceCalculationOut(**(details.get("calculation") or {}))

    evaluated_version_number: int | None = None
    if document.relevance_version_id is not None:
        for version in document.versions or []:
            if version.id == document.relevance_version_id:
                evaluated_version_number = version.version_number
                break

    is_stale = (
        document.relevance_version_id is not None
        and document.current_version_id is not None
        and document.relevance_version_id != document.current_version_id
    )

    return RelevanceExplanationOut(
        engine=details.get("engine", document.relevance_engine or "unknown"),
        is_maritime=bool(details.get("is_maritime", document.is_maritime)),
        score=int(details.get("score", document.maritime_score)),
        classification=details.get("classification", "not_maritime"),
        reason=details.get("reason", ""),
        matched_terms=list(details.get("matched_terms") or []),
        concepts=list(details.get("concepts") or []),
        calculation=calculation,
        matches=[RelevanceTermOut(**m) for m in (details.get("matches") or [])],
        negative_matches=[
            RelevanceTermOut(**m) for m in (details.get("negative_matches") or [])
        ],
        evaluated_version_number=evaluated_version_number,
        evaluated_version_id=document.relevance_version_id,
        is_stale=is_stale,
    )


def document_detail(document: Document) -> DocumentDetailOut:
    current = document.current_version
    versions = sorted(
        document.versions or [], key=lambda v: v.version_number, reverse=True
    )
    metadata = (current.metadata_json or {}) if current else {}

    return DocumentDetailOut(
        **_summary_fields(document),
        source=document.source,
        content=(current.content if current else ""),
        relevance=relevance_explanation(document),
        versions=[
            version_summary(v, current_id=document.current_version_id) for v in versions
        ],
        change_log=[
            ChangeLogEntryOut.model_validate(entry) for entry in (document.change_log or [])
        ],
        normalized_metadata=metadata.get("normalized"),
        source_metadata=metadata.get("source"),
        created_at=document.created_at,
        updated_at=document.updated_at,
        synthetic_notice=SYNTHETIC_DATA_NOTICE if document.is_synthetic else None,
    )


def import_run(run: ImportRun) -> ImportRunOut:
    return ImportRunOut(
        id=run.id,
        source=run.source,
        client_kind=run.client_kind,
        trigger=run.trigger,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=run.duration_seconds,
        documents_checked=run.documents_checked,
        documents_created=run.documents_created,
        documents_updated=run.documents_updated,
        documents_unchanged=run.documents_unchanged,
        documents_rejected=run.documents_rejected,
        documents_failed=run.documents_failed,
        status=run.status,
        error_message=run.error_message,
        errors=run.errors,
        used_synthetic_data=run.client_kind == "fixture",
    )

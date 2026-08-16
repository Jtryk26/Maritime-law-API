"""Oversættelse fra ORM-modeller til API-skemaer.

Samlet ét sted, så et dokument ser ens ud i søgeresultater, lister og
detaljevisning — og så forklaringen på den maritime klassifikation
altid bygges på samme måde.
"""

from __future__ import annotations

from typing import Any

from app.models import Document, DocumentVersion, ImportRun, SearchQueryLog
from app.schemas import (
    SYNTHETIC_DATA_NOTICE,
    ChangeLogEntryOut,
    DocumentCategoryOut,
    DocumentDetailOut,
    DocumentStructureOut,
    DocumentSummaryOut,
    ImportRunOut,
    LoggedQueryOut,
    ParagraphOut,
    QueryIntentOut,
    RankingAdjustmentOut,
    RankingBreakdownOut,
    RelevanceCalculationOut,
    RelevanceExplanationOut,
    RelevanceTermOut,
    SearchHitOut,
    SimilarDocumentOut,
    StructureChapterOut,
    StructureParagraphOut,
    VersionSummaryOut,
)
from app.services.legal import parse_legal_structure
from app.services.ranking import LawClass

__all__ = [
    "document_summary",
    "document_detail",
    "document_structure",
    "logged_query",
    "paragraph_hit",
    "query_intent",
    "ranking_breakdown",
    "search_hit",
    "similar_document",
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
        # Begge titler med i ethvert svar. Brugerfladen skal kunne vise den
        # korte uden at gætte, og den fulde uden et ekstra kald.
        "original_title": document.title,
        "display_title": document.display_title or document.title,
        "law_class": document.law_class,
        "law_class_label": (
            LawClass.LABELS.get(document.law_class) if document.law_class else None
        ),
        "scope_score": (
            round(document.scope_score, 3) if document.scope_score is not None else 0.55
        ),
        "authority_score": (
            round(document.authority_score, 3)
            if document.authority_score is not None
            else 0.5
        ),
        "niche_groups": list(document.niche_groups or []),
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


def paragraph_hit(hit) -> ParagraphOut | None:
    """Oversætter en :class:`ParagraphHit` til API-form."""
    if hit is None:
        return None
    return ParagraphOut(
        paragraph_id=hit.paragraph_id,
        chapter_no=hit.chapter_no,
        chapter_title=hit.chapter_title,
        section_title=hit.section_title,
        legal_path=hit.legal_path,
        full_citation=hit.full_citation,
        snippet=hit.snippet,
    )


def ranking_breakdown(breakdown) -> RankingBreakdownOut | None:
    """Regnestykket bag et resultats placering."""
    if breakdown is None:
        return None
    return RankingBreakdownOut(
        lexical_score=round(breakdown.lexical_score, 4),
        semantic_score=round(breakdown.semantic_score, 4),
        authority_score=round(breakdown.authority_score, 4),
        scope_score=round(breakdown.scope_score, 4),
        maritime_score=round(breakdown.maritime_score, 4),
        status_score=round(breakdown.status_score, 4),
        base_score=round(breakdown.base_score, 4),
        multiplier=round(breakdown.multiplier, 4),
        final_score=round(breakdown.final_score, 6),
        adjustments=[
            RankingAdjustmentOut(
                name=a.name,
                factor=round(a.factor, 3),
                reason=a.reason,
                percent=round((a.factor - 1.0) * 100),
            )
            for a in breakdown.adjustments
        ],
    )


def query_intent(intent) -> QueryIntentOut | None:
    if intent is None:
        return None
    return QueryIntentOut(**intent.to_json())


def search_hit(
    document: Document,
    *,
    rank: float,
    snippet: str,
    lexical_rank: float | None = None,
    semantic_score: float | None = None,
    match_source: str = "lexical",
    matched_heading: str | None = None,
    paragraph=None,
    paragraphs=None,
    ranking=None,
) -> SearchHitOut:
    return SearchHitOut(
        **_summary_fields(document),
        rank=round(rank, 4),
        snippet=snippet,
        lexical_rank=round(lexical_rank, 4) if lexical_rank is not None else None,
        semantic_score=round(semantic_score, 4) if semantic_score is not None else None,
        match_source=match_source,
        matched_heading=matched_heading,
        paragraph=paragraph_hit(paragraph),
        paragraphs=[p for p in (paragraph_hit(item) for item in (paragraphs or [])) if p],
        ranking=ranking_breakdown(ranking),
    )


def similar_document(
    document: Document, *, similarity: float, matched_heading: str | None, excerpt: str
) -> SimilarDocumentOut:
    return SimilarDocumentOut(
        **_summary_fields(document),
        similarity=round(similarity, 4),
        matched_heading=matched_heading,
        excerpt=excerpt,
    )


def logged_query(entry: SearchQueryLog) -> LoggedQueryOut:
    return LoggedQueryOut(
        id=entry.id,
        query=entry.query_text,
        occurrences=entry.occurrences,
        last_result_count=entry.last_result_count,
        best_result_count=entry.best_result_count,
        last_mode=entry.last_mode,
        first_seen_at=entry.first_seen_at,
        last_seen_at=entry.last_seen_at,
    )


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


def document_structure(content: str, *, document_title: str | None = None) -> DocumentStructureOut:
    """Dokumentets juridiske form, klar til en læsevisning.

    Præamblen holdes adskilt fra brødteksten, så brugerfladen kan vise en
    regel i første skærmbillede og folde kundgørelsesformlen sammen.
    """
    structure = parse_legal_structure(content or "", document_title=document_title)
    if not structure.text:
        return DocumentStructureOut()

    def _paragraph(item) -> StructureParagraphOut:
        return StructureParagraphOut(
            paragraph_id=item.paragraph_id,
            sort_key=item.sort_key,
            heading=item.heading,
            text=item.text,
        )

    chapters: list[StructureChapterOut] = []
    by_chapter: dict[str, StructureChapterOut] = {}
    for chapter in structure.chapters:
        entry = StructureChapterOut(
            number=chapter.number,
            title=chapter.title,
            section_no=chapter.section_no,
            section_title=chapter.section_title,
        )
        chapters.append(entry)
        by_chapter[chapter.number] = entry

    loose: list[StructureParagraphOut] = []
    for paragraph in structure.paragraphs:
        target = by_chapter.get(paragraph.chapter_no or "")
        (target.paragraphs if target is not None else loose).append(_paragraph(paragraph))

    return DocumentStructureOut(
        has_paragraphs=structure.has_paragraphs,
        preamble=structure.preamble,
        chapters=[c for c in chapters if c.paragraphs],
        loose_paragraphs=loose,
        paragraph_count=len(structure.paragraphs),
    )


def document_detail(document: Document) -> DocumentDetailOut:
    current = document.current_version
    versions = sorted(
        document.versions or [], key=lambda v: v.version_number, reverse=True
    )
    metadata = (current.metadata_json or {}) if current else {}
    content = current.content if current else ""

    return DocumentDetailOut(
        **_summary_fields(document),
        source=document.source,
        content=content,
        structure=document_structure(
            content, document_title=document.display_title or document.title
        ),
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

"""Ruter for dokumenter, søgning og kategorier."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, Pagination
from app.api.serializers import (
    document_detail,
    document_summary,
    logged_query,
    search_hit,
    similar_document,
    version_summary,
)
from app.models import Category, Document, DocumentCategory, DocumentVersion
from app.schemas import (
    CategoryWithCount,
    DocumentDetailOut,
    DocumentSummaryOut,
    FacetsOut,
    FacetValue,
    LoggedQueryOut,
    Page,
    RelatedQueryOut,
    SearchResponse,
    SimilarDocumentOut,
    VersionDetailOut,
    VersionSummaryOut,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.search import (
    QueryLogService,
    SearchQuery,
    VectorSearchBackend,
    get_search_backend,
    resolve_search_mode,
)

logger = get_logger(__name__)

router = APIRouter()


def _document_query():
    """Basisforespørgsel med ivrig indlæsning af relationer."""
    return select(Document).options(
        selectinload(Document.category_links).selectinload(DocumentCategory.category),
        selectinload(Document.versions),
        selectinload(Document.current_version),
    )


def _load_document(session, document_id: int) -> Document:
    document = session.scalars(
        _document_query()
        .options(selectinload(Document.change_log))
        .where(Document.id == document_id)
    ).first()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dokument med id {document_id} findes ikke.",
        )
    return document


# ---------------------------------------------------------------------------
# Søgning
# ---------------------------------------------------------------------------


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Søg i maritim dansk lovgivning",
    tags=["søgning"],
)
def search_documents(
    session: DbSession,
    pagination: Pagination,
    q: Annotated[str | None, Query(description="Fritekstsøgning i titel, tekst, myndighed og kategorier.")] = None,
    category: Annotated[list[str] | None, Query(description="Kategori-slug. Kan angives flere gange.")] = None,
    document_type: Annotated[list[str] | None, Query(description="Dokumenttype, f.eks. Bekendtgørelse.")] = None,
    authority: Annotated[list[str] | None, Query(description="Udstedende myndighed.")] = None,
    status_filter: Annotated[list[str] | None, Query(alias="status", description="Retlig status, f.eks. Gældende.")] = None,
    document_number: Annotated[str | None, Query(description="Eksakt lov-/bekendtgørelsesnummer.")] = None,
    published_from: Annotated[date | None, Query(description="Publiceret fra og med.")] = None,
    published_to: Annotated[date | None, Query(description="Publiceret til og med.")] = None,
    min_score: Annotated[int | None, Query(ge=0, le=100, description="Mindste maritime score.")] = None,
    max_score: Annotated[int | None, Query(ge=0, le=100, description="Højeste maritime score.")] = None,
    is_maritime: Annotated[bool | None, Query(description="Filtrér på maritim klassifikation.")] = None,
    sort: Annotated[str, Query(pattern="^(relevance|date_desc|date_asc|score_desc|title)$")] = "relevance",
    mode: Annotated[
        str | None,
        Query(
            pattern="^(lexical|semantic|hybrid)$",
            description=(
                "lexical = ordene skal stå der. semantic = betydningen skal ligne. "
                "hybrid = begge dele smeltet sammen (standard). Kan ikke leveres "
                "semantisk uden vektorer; svarets 'mode' viser hvad der faktisk skete."
            ),
        ),
    ] = None,
    related: Annotated[
        bool, Query(description="Medtag lignende tidligere søgninger i svaret.")
    ] = True,
) -> SearchResponse:
    """Søgning med facetfiltre, i tre tilstande.

    Den leksikalske del dækker titel, dokumentnummer, myndighed,
    dokumenttype, kategorinavne og selve lovteksten; titelmatch rangerer
    højest. Den semantiske del sammenligner søgningens betydning med
    vektoriserede stykker lovtekst og finder derfor også dokumenter, der
    bruger andre ord end søgningen.

    Søgningen logges (uden bruger- eller IP-oplysninger), så systemet kan
    vise hvad der ellers søges efter, og hvilke søgninger der aldrig
    giver svar.
    """
    page, page_size = pagination
    effective_mode, notice = resolve_search_mode(session, mode)

    query = SearchQuery(
        q=q,
        categories=category or [],
        document_types=document_type or [],
        authorities=authority or [],
        statuses=status_filter or [],
        document_number=document_number,
        published_from=published_from,
        published_to=published_to,
        min_score=min_score,
        max_score=max_score,
        is_maritime=is_maritime,
        sort=sort,
        mode=effective_mode,
        page=page,
        page_size=page_size,
    )

    backend = get_search_backend(session, effective_mode)
    results = backend.search(session, query)

    # Loggen skrives EFTER resultatet er fundet, og kan aldrig vælte
    # søgningen: QueryLogService fanger sine egne fejl.
    related_queries: list[RelatedQueryOut] = []
    if q and q.strip():
        log_service = QueryLogService(session)
        log_service.record(q, result_count=results.total, mode=results.mode)
        if related:
            related_queries = [
                RelatedQueryOut(**item.to_json()) for item in log_service.related(q, limit=5)
            ]

    return SearchResponse(
        items=[
            search_hit(
                hit.document,
                rank=hit.rank,
                snippet=hit.snippet,
                lexical_rank=hit.lexical_rank,
                semantic_score=hit.semantic_score,
                match_source=hit.match_source,
                matched_heading=hit.matched_heading,
            )
            for hit in results.hits
        ],
        total=results.total,
        page=results.page,
        page_size=results.page_size,
        total_pages=results.total_pages,
        query=q,
        backend=results.backend,
        mode=results.mode,
        semantic_available=results.semantic_available,
        truncated=results.truncated,
        notice=results.notice or notice,
        related_queries=related_queries,
        applied_filters={
            "categories": query.categories,
            "document_types": query.document_types,
            "authorities": query.authorities,
            "statuses": query.statuses,
            "document_number": query.document_number,
            "published_from": query.published_from,
            "published_to": query.published_to,
            "min_score": query.min_score,
            "max_score": query.max_score,
            "is_maritime": query.is_maritime,
            "sort": query.sort,
            "mode": results.mode,
        },
    )


# ---------------------------------------------------------------------------
# Dokumenter
# ---------------------------------------------------------------------------


@router.get(
    "/documents",
    response_model=Page[DocumentSummaryOut],
    summary="List dokumenter",
    tags=["dokumenter"],
)
def list_documents(
    session: DbSession,
    pagination: Pagination,
    is_maritime: Annotated[bool | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    sort: Annotated[str, Query(pattern="^(date_desc|date_asc|score_desc|title)$")] = "date_desc",
) -> Page[DocumentSummaryOut]:
    """Simpel liste over dokumenter uden fritekstsøgning."""
    page, page_size = pagination
    stmt = _document_query()

    if is_maritime is not None:
        stmt = stmt.where(Document.is_maritime.is_(is_maritime))
    if category:
        stmt = stmt.where(
            Document.id.in_(
                select(DocumentCategory.document_id)
                .join(Category, Category.id == DocumentCategory.category_id)
                .where(Category.slug.in_(category))
            )
        )

    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0

    if sort == "date_asc":
        stmt = stmt.order_by(Document.published_date.asc().nullsfirst(), Document.id.asc())
    elif sort == "score_desc":
        stmt = stmt.order_by(Document.maritime_score.desc(), Document.id.desc())
    elif sort == "title":
        stmt = stmt.order_by(Document.title.asc())
    else:
        stmt = stmt.order_by(Document.published_date.desc().nullslast(), Document.id.desc())

    documents = session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()

    return Page[DocumentSummaryOut](
        items=[document_summary(d) for d in documents],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size else 0,
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetailOut,
    summary="Hent ét dokument med metadata, tekst, versioner og forklaring",
    tags=["dokumenter"],
)
def get_document(
    session: DbSession,
    document_id: Annotated[int, Path(ge=1)],
) -> DocumentDetailOut:
    return document_detail(_load_document(session, document_id))


@router.get(
    "/documents/{document_id}/versions",
    response_model=list[VersionSummaryOut],
    summary="Versionshistorik for et dokument",
    tags=["dokumenter"],
)
def get_document_versions(
    session: DbSession,
    document_id: Annotated[int, Path(ge=1)],
) -> list[VersionSummaryOut]:
    document = _load_document(session, document_id)
    versions = sorted(document.versions, key=lambda v: v.version_number, reverse=True)
    return [version_summary(v, current_id=document.current_version_id) for v in versions]


@router.get(
    "/documents/{document_id}/versions/{version_number}",
    response_model=VersionDetailOut,
    summary="Hent indholdet af en bestemt version",
    tags=["dokumenter"],
)
def get_document_version(
    session: DbSession,
    document_id: Annotated[int, Path(ge=1)],
    version_number: Annotated[int, Path(ge=1)],
) -> VersionDetailOut:
    """Historiske versioner bevares uændret og kan altid hentes frem."""
    document = _load_document(session, document_id)
    version = session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_number == version_number,
        )
    ).first()

    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {version_number} findes ikke for dokument {document_id}.",
        )

    return VersionDetailOut(
        id=version.id,
        version_number=version.version_number,
        content_hash=version.content_hash,
        content_length=len(version.content or ""),
        retrieved_at=version.retrieved_at,
        created_at=version.created_at,
        is_current=version.id == document.current_version_id,
        content=version.content or "",
        metadata_json=version.metadata_json,
    )


# ---------------------------------------------------------------------------
# Kategorier og facetter
# ---------------------------------------------------------------------------


@router.get(
    "/categories",
    response_model=list[CategoryWithCount],
    summary="Maritim taksonomi med dokumenttællinger",
    tags=["kategorier"],
)
def list_categories(session: DbSession) -> list[CategoryWithCount]:
    counts = dict(
        session.execute(
            select(DocumentCategory.category_id, func.count(DocumentCategory.document_id))
            .group_by(DocumentCategory.category_id)
        ).all()
    )
    categories = session.scalars(select(Category).order_by(Category.sort_order)).all()
    return [
        CategoryWithCount(
            id=c.id,
            slug=c.slug,
            name=c.name,
            description=c.description,
            sort_order=c.sort_order,
            document_count=counts.get(c.id, 0),
        )
        for c in categories
    ]


@router.get(
    "/facets",
    response_model=FacetsOut,
    summary="Tilgængelige filterværdier",
    tags=["søgning"],
)
def get_facets(session: DbSession) -> FacetsOut:
    """Leverer filtermulighederne, så brugerfladen ikke hardcoder dem."""

    def _values(column) -> list[FacetValue]:
        rows = session.execute(
            select(column, func.count(Document.id))
            .where(column.is_not(None))
            .group_by(column)
            .order_by(func.count(Document.id).desc())
        ).all()
        return [FacetValue(value=str(value), count=count) for value, count in rows]

    return FacetsOut(
        document_types=_values(Document.document_type),
        authorities=_values(Document.authority),
        statuses=_values(Document.status),
        categories=list_categories(session),
    )


# ---------------------------------------------------------------------------
# Semantisk: lignende dokumenter og søgelog
# ---------------------------------------------------------------------------


@router.get(
    "/documents/{document_id}/similar",
    response_model=list[SimilarDocumentOut],
    summary="Dokumenter der ligner dette",
    tags=["søgning"],
)
def similar_documents(
    session: DbSession,
    document_id: Annotated[int, Path(ge=1)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SimilarDocumentOut]:
    """Finder beslægtede dokumenter ud fra dokumentets egne vektorer.

    Sammenligningen sker på dokumentets samlede retning i vektorrummet —
    gennemsnittet af dets stykker — og finder derfor beslægtet regulering,
    også hvor titlerne ikke ligner hinanden.

    Tom liste, hvis dokumentet endnu ikke er vektoriseret. Det er ikke en
    fejl: kør `python -m app.cli embed run`.
    """
    document = _load_document(session, document_id)

    settings = get_settings()
    if not settings.embeddings_enabled:
        return []

    try:
        backend = VectorSearchBackend()
        matches = backend.similar_to_document(session, document.id, limit=limit)
    except Exception as exc:  # noqa: BLE001 - manglende model er ikke en serverfejl
        logger.info(
            "api.similar.unavailable",
            extra={"document_id": document_id, "error": str(exc)},
        )
        return []

    documents = {
        d.id: d
        for d in session.scalars(
            _document_query().where(Document.id.in_([m.document_id for m in matches]))
        ).all()
    }

    results: list[SimilarDocumentOut] = []
    for match in matches:
        found = documents.get(match.document_id)
        if found is None:
            continue
        results.append(
            similar_document(
                found,
                similarity=match.similarity,
                matched_heading=match.chunk.heading,
                excerpt=match.chunk.content[:300],
            )
        )
    return results


@router.get(
    "/search/queries",
    response_model=list[LoggedQueryOut],
    summary="Loggede søgninger",
    tags=["søgning"],
)
def logged_queries(
    session: DbSession,
    kind: Annotated[
        str,
        Query(
            pattern="^(popular|without_results)$",
            description=(
                "popular = de hyppigste søgninger. "
                "without_results = søgninger der aldrig har givet et resultat."
            ),
        ),
    ] = "popular",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[LoggedQueryOut]:
    """Hvad der bliver søgt efter.

    Loggen indeholder søgestrenge, antal forekomster og antal træf — og
    hverken bruger, IP-adresse eller session. `without_results` er den
    interessante liste: den viser enten hvad materialet mangler, eller
    hvor brugernes ordvalg og lovtekstens går fra hinanden.
    """
    service = QueryLogService(session)
    entries = (
        service.popular(limit=limit)
        if kind == "popular"
        else service.without_results(limit=limit)
    )
    return [logged_query(entry) for entry in entries]


@router.get(
    "/search/related",
    response_model=list[RelatedQueryOut],
    summary="Tidligere søgninger der ligner denne",
    tags=["søgning"],
)
def related_queries(
    session: DbSession,
    q: Annotated[str, Query(min_length=1, description="Søgestrengen der sammenlignes med.")],
    limit: Annotated[int, Query(ge=1, le=25)] = 5,
) -> list[RelatedQueryOut]:
    """Beslægtede søgninger, fundet på vektorlighed frem for fælles ord."""
    service = QueryLogService(session)
    return [RelatedQueryOut(**item.to_json()) for item in service.related(q, limit=limit)]

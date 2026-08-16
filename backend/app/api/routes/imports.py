"""Ruter for import, driftsstatistik og systemtilstand."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import func, select

from app.api.deps import DbSession, Pagination
from app.api.serializers import import_run as serialize_run
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import (
    Category,
    Document,
    DocumentCategory,
    DocumentVersion,
    ImportRun,
)
from app.schemas import (
    AdminSessionOut,
    CategoryWithCount,
    EmbeddingRunOut,
    EmbeddingRunRequest,
    EmbeddingStatusOut,
    ImportRequest,
    ImportRunOut,
    Page,
    StatsOut,
)
from app.services.categorization import get_categorization_engine
from app.services.importer import ImportService
from app.services.relevance import get_relevance_engine
from app.services.retsinformation import build_source_client
from app.services.embedding import EmbeddingIndexer, get_embedding_provider
from app.services.search import QueryLogService, get_search_backend

logger = get_logger(__name__)

# HELE denne router kræver administratortoken — se app/api/routes/__init__.py.
# Alt herunder er enten en skrivehandling eller driftsdata.
router = APIRouter()


# ---------------------------------------------------------------------------
# Adgang
# ---------------------------------------------------------------------------


@router.get(
    "/admin/session",
    response_model=AdminSessionOut,
    summary="Kontrollér administratortoken",
    tags=["drift"],
)
def check_admin_session() -> AdminSessionOut:
    """Svarer 200, hvis tokenet er gyldigt — ellers 401 fra dependencyen.

    Brugerfladen bruger det som "log ind": tokenet afprøves én gang, før
    driftssiden vises.
    """
    settings = get_settings()
    return AdminSessionOut(environment=settings.environment, app_name=settings.app_name)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@router.post(
    "/import/run",
    response_model=ImportRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Kør en import nu",
    tags=["import"],
)
def run_import(session: DbSession, request: ImportRequest | None = None) -> ImportRunOut:
    """Starter en import synkront og returnerer resultatet.

    Kilden vælges eksplicit. Er `source_client` udeladt, bruges
    konfigurationens SOURCE_CLIENT. Der falder aldrig automatisk tilbage
    til syntetiske data — mislykkes produktionskilden, fejler kaldet.

    Importen kører synkront i Version 1. Det er tilstrækkeligt til det
    datavolumen ændringsfeeden leverer, og gør adfærden let at forstå.
    """
    payload = request or ImportRequest()

    client = build_source_client(
        payload.source_client, fixture_revision=payload.fixture_revision
    )
    try:
        service = ImportService(
            session,
            client=client,
            relevance_engine=get_relevance_engine(),
            categorization_engine=get_categorization_engine(),
        )
        summary = service.run(since=payload.since, trigger="api", limit=payload.limit)
    finally:
        client.close()

    run = session.get(ImportRun, summary.import_run_id)
    if run is None:  # pragma: no cover - bør ikke kunne ske
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Importkørslen blev ikke registreret.",
        )
    return serialize_run(run)


@router.get(
    "/import/runs",
    response_model=Page[ImportRunOut],
    summary="Importhistorik",
    tags=["import"],
)
def list_import_runs(session: DbSession, pagination: Pagination) -> Page[ImportRunOut]:
    page, page_size = pagination
    total = session.scalar(select(func.count(ImportRun.id))) or 0
    runs = session.scalars(
        select(ImportRun)
        .order_by(ImportRun.started_at.desc(), ImportRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return Page[ImportRunOut](
        items=[serialize_run(r) for r in runs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size else 0,
    )


@router.get(
    "/import/runs/{run_id}",
    response_model=ImportRunOut,
    summary="Hent én importkørsel",
    tags=["import"],
)
def get_import_run(session: DbSession, run_id: Annotated[int, Path(ge=1)]) -> ImportRunOut:
    run = session.get(ImportRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Importkørsel {run_id} findes ikke.",
        )
    return serialize_run(run)


# ---------------------------------------------------------------------------
# Statistik
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=StatsOut, summary="Nøgletal", tags=["drift"])
def get_stats(session: DbSession) -> StatsOut:
    settings = get_settings()

    documents_total = session.scalar(select(func.count(Document.id))) or 0
    documents_maritime = (
        session.scalar(select(func.count(Document.id)).where(Document.is_maritime.is_(True))) or 0
    )
    documents_synthetic = (
        session.scalar(select(func.count(Document.id)).where(Document.is_synthetic.is_(True))) or 0
    )
    versions_total = session.scalar(select(func.count(DocumentVersion.id))) or 0
    categories_total = session.scalar(select(func.count(Category.id))) or 0
    average_score = session.scalar(select(func.avg(Document.maritime_score))) or 0.0

    # "Mulig maritim relevans": gemt, men ikke over den maritime tærskel.
    documents_possible = (
        session.scalar(
            select(func.count(Document.id)).where(Document.is_maritime.is_(False))
        )
        or 0
    )

    by_status = dict(
        session.execute(
            select(Document.status, func.count(Document.id))
            .where(Document.status.is_not(None))
            .group_by(Document.status)
        ).all()
    )
    by_type = dict(
        session.execute(
            select(Document.document_type, func.count(Document.id))
            .where(Document.document_type.is_not(None))
            .group_by(Document.document_type)
        ).all()
    )

    category_rows = session.execute(
        select(Category, func.count(DocumentCategory.document_id).label("n"))
        .join(DocumentCategory, DocumentCategory.category_id == Category.id)
        .group_by(Category.id)
        .order_by(func.count(DocumentCategory.document_id).desc())
        .limit(10)
    ).all()

    last_run = session.scalars(
        select(ImportRun).order_by(ImportRun.started_at.desc(), ImportRun.id.desc()).limit(1)
    ).first()

    return StatsOut(
        documents_total=documents_total,
        documents_maritime=documents_maritime,
        documents_possible=documents_possible,
        documents_synthetic=documents_synthetic,
        versions_total=versions_total,
        categories_total=categories_total,
        average_maritime_score=round(float(average_score), 1),
        documents_by_status={str(k): v for k, v in by_status.items()},
        documents_by_type={str(k): v for k, v in by_type.items()},
        top_categories=[
            CategoryWithCount(
                id=c.id,
                slug=c.slug,
                name=c.name,
                description=c.description,
                sort_order=c.sort_order,
                document_count=n,
            )
            for c, n in category_rows
        ],
        last_import=serialize_run(last_run) if last_run else None,
        source_client=settings.source_client,
        database_backend=session.get_bind().dialect.name,
        search_backend=get_search_backend(session).name,
        embeddings=embedding_status(session),
        search_log=QueryLogService(session).stats(),
    )


def embedding_status(session) -> EmbeddingStatusOut:
    """Tilstanden for det semantiske indeks.

    Fejler aldrig: kan modellen ikke indlæses, rapporteres netop det i
    `error`. Driftsvisningen skal kunne fortælle at vektorsøgning er ude
    af drift — ikke gå ned sammen med den.
    """
    settings = get_settings()
    if not settings.embeddings_enabled:
        return EmbeddingStatusOut(enabled=False)

    try:
        provider = get_embedding_provider()
        coverage = EmbeddingIndexer(session, provider).coverage()
    except Exception as exc:  # noqa: BLE001
        logger.info("api.stats.embeddings_unavailable", extra={"error": str(exc)})
        return EmbeddingStatusOut(enabled=True, error=str(exc))

    return EmbeddingStatusOut(enabled=True, **coverage)


# ---------------------------------------------------------------------------
# Semantisk indeks
# ---------------------------------------------------------------------------


@router.get(
    "/embeddings/status",
    response_model=EmbeddingStatusOut,
    summary="Tilstand for det semantiske indeks",
    tags=["drift"],
)
def get_embedding_status(session: DbSession) -> EmbeddingStatusOut:
    """Hvor stor en del af det maritime materiale der er vektoriseret."""
    return embedding_status(session)


@router.post(
    "/embeddings/run",
    response_model=EmbeddingRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Vektorisér de dokumenter der mangler",
    tags=["drift"],
)
def run_embedding(
    session: DbSession, request: EmbeddingRunRequest | None = None
) -> EmbeddingRunOut:
    """Bygger det semantiske indeks videre.

    Kører synkront ligesom importen, men med et lavt standardloft: en
    CPU-model bruger i størrelsesordenen et sekund pr. dokument, og en
    HTTP-forespørgsel skal ikke holdes åben i en time. Hele indekset
    bygges fra kommandolinjen::

        python -m app.cli embed run

    Vektorisering sker bevidst ikke under importen. Lovteksten er det
    vigtige; vektorerne er et indeks over den, og en import må ikke kunne
    fejle, fordi en model ikke kunne indlæses.
    """
    payload = request or EmbeddingRunRequest()
    settings = get_settings()

    if not settings.embeddings_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Vektorlaget er slået fra (EMBEDDINGS_ENABLED=false). "
                "Slå det til i konfigurationen først."
            ),
        )

    try:
        provider = get_embedding_provider()
    except Exception as exc:  # noqa: BLE001
        # Miskonfiguration eller manglende model. 503 frem for 500: det er
        # ikke en fejl i forespørgslen, og den kan lykkes senere.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Embedding-modellen er ikke tilgængelig: {exc}",
        ) from exc

    indexer = EmbeddingIndexer(session, provider)
    try:
        report = indexer.index_pending(
            limit=payload.limit,
            only_maritime=not payload.include_non_maritime,
            reset=payload.reset,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("api.embeddings.run_failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Vektorisering mislykkedes: {exc}",
        ) from exc

    return EmbeddingRunOut(
        **report.to_json(),
        pending_after=indexer.pending_count(only_maritime=not payload.include_non_maritime),
    )

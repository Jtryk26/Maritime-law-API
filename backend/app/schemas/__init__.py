"""Pydantic-skemaer for API'ets svar.

SQLAlchemy-modeller returneres aldrig direkte. Skemaerne udgør API'ets
kontrakt og gør det muligt at ændre databasen uden at bryde klienter.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

# Vises alle steder hvor lovtekst præsenteres.
LEGAL_SOURCE_NOTICE = (
    "Dokumentdata er hentet fra Retsinformation. Kontrollér altid den gældende "
    "officielle tekst på Retsinformation ved juridisk anvendelse."
)

SYNTHETIC_DATA_NOTICE = (
    "ADVARSEL: Dette dokument stammer fra systemets syntetiske testdata og er "
    "IKKE hentet fra Retsinformation. Det er ikke gældende ret og må ikke "
    "anvendes juridisk."
)


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Fælles
# ---------------------------------------------------------------------------


class Page(BaseModel, Generic[T]):
    """Sideinddelt svar."""

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class ErrorResponse(BaseModel):
    """Ensartet fejlformat."""

    detail: str
    error_type: str = "error"


# ---------------------------------------------------------------------------
# Kategorier
# ---------------------------------------------------------------------------


class CategoryOut(ORMBase):
    id: int
    slug: str
    name: str
    description: str | None = None
    sort_order: int = 0


class CategoryWithCount(CategoryOut):
    document_count: int = 0


class DocumentCategoryOut(BaseModel):
    """Kategori tildelt et dokument, med begrundelse."""

    slug: str
    name: str
    confidence: float
    matched_terms: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Relevansforklaring
# ---------------------------------------------------------------------------


class RelevanceTermOut(BaseModel):
    """Et enkelt termbidrag til den maritime score."""

    term: str
    field: str
    occurrences: int
    counted_occurrences: int
    capped: bool = False
    term_weight: float
    field_weight: float
    contribution: float
    concept: str | None = None


class RelevanceCalculationOut(BaseModel):
    """Regnestykket bag scoren, så vurderingen kan efterprøves."""

    positive_raw: float = 0.0
    concept_bonus: float = 0.0
    negative_raw: float = 0.0
    raw_score: float = 0.0
    saturation: float = 0.0
    normalized_score: int = 0
    title_floor_applied: bool = False
    title_floor_terms: list[str] = Field(default_factory=list)
    field_contributions: dict[str, float] = Field(default_factory=dict)
    thresholds: dict[str, int] = Field(default_factory=dict)


class RelevanceExplanationOut(BaseModel):
    """Fuld forklaring på hvorfor et dokument blev klassificeret som det blev."""

    engine: str = "unknown"
    is_maritime: bool = False
    score: int = 0
    classification: str = "not_maritime"
    reason: str = ""
    matched_terms: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    calculation: RelevanceCalculationOut = Field(default_factory=RelevanceCalculationOut)
    matches: list[RelevanceTermOut] = Field(default_factory=list)
    negative_matches: list[RelevanceTermOut] = Field(default_factory=list)
    #: Hvilken version af teksten vurderingen blev beregnet på.
    evaluated_version_number: int | None = None
    evaluated_version_id: int | None = None
    #: True hvis dokumentet siden er ændret, så vurderingen kan være forældet.
    is_stale: bool = False


# ---------------------------------------------------------------------------
# Versioner
# ---------------------------------------------------------------------------


class VersionSummaryOut(ORMBase):
    """Version uden indhold — til versionshistorik."""

    id: int
    version_number: int
    content_hash: str
    content_length: int = 0
    retrieved_at: datetime | None = None
    created_at: datetime
    is_current: bool = False


class VersionDetailOut(VersionSummaryOut):
    """Version med fuldt indhold."""

    content: str = ""
    metadata_json: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Dokumenter
# ---------------------------------------------------------------------------


class DocumentSummaryOut(BaseModel):
    """Dokument i lister og søgeresultater.

    Indeholder nok til at forstå resultatet uden at åbne dokumentet.
    """

    id: int
    source_id: str
    retsinformation_id: str | None = None
    document_number: str | None = None
    title: str
    short_title: str | None = None
    document_type: str | None = None
    authority: str | None = None
    published_date: date | None = None
    effective_date: date | None = None
    status: str | None = None
    is_maritime: bool = False
    maritime_score: int = 0
    classification: str = "not_maritime"
    categories: list[DocumentCategoryOut] = Field(default_factory=list)
    source_url: str | None = None
    is_synthetic: bool = False
    current_version_number: int | None = None
    version_count: int = 0
    last_retrieved_at: datetime | None = None


class SearchHitOut(DocumentSummaryOut):
    """Søgeresultat med rangering og tekstuddrag."""

    rank: float = 0.0
    snippet: str = ""


class ChangeLogEntryOut(ORMBase):
    id: int
    change_type: str
    detail: str | None = None
    old_version_id: int | None = None
    new_version_id: int | None = None
    import_run_id: int | None = None
    created_at: datetime


class DocumentDetailOut(DocumentSummaryOut):
    """Fuldt dokument til detaljevisning."""

    source: str
    content: str = ""
    relevance: RelevanceExplanationOut = Field(default_factory=RelevanceExplanationOut)
    versions: list[VersionSummaryOut] = Field(default_factory=list)
    change_log: list[ChangeLogEntryOut] = Field(default_factory=list)
    #: Kildens egne metadata, bevaret adskilt fra de normaliserede felter.
    source_metadata: dict[str, Any] | None = None
    normalized_metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    legal_notice: str = LEGAL_SOURCE_NOTICE
    synthetic_notice: str | None = None


class SearchResponse(BaseModel):
    """Søgesvar med resultater og anvendte filtre."""

    items: list[SearchHitOut]
    total: int
    page: int
    page_size: int
    total_pages: int
    query: str | None = None
    backend: str = "fallback"
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    legal_notice: str = LEGAL_SOURCE_NOTICE


class FacetValue(BaseModel):
    value: str
    count: int


class FacetsOut(BaseModel):
    """Tilgængelige filterværdier, så brugerfladen ikke hardcoder dem."""

    document_types: list[FacetValue] = Field(default_factory=list)
    authorities: list[FacetValue] = Field(default_factory=list)
    statuses: list[FacetValue] = Field(default_factory=list)
    categories: list[CategoryWithCount] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Import og drift
# ---------------------------------------------------------------------------


class ImportRunOut(ORMBase):
    id: int
    source: str
    client_kind: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    documents_checked: int = 0
    documents_created: int = 0
    documents_updated: int = 0
    documents_unchanged: int = 0
    documents_rejected: int = 0
    documents_failed: int = 0
    status: str
    error_message: str | None = None
    errors: list[dict[str, Any]] | None = None
    #: True når kørslen brugte syntetiske fixturdata.
    used_synthetic_data: bool = False


class ImportRequest(BaseModel):
    """Anmodning om at køre en import."""

    source_client: Literal["fixture", "production"] | None = Field(
        default=None,
        description=(
            "Hvilken kilde der skal bruges. Udelades den, benyttes SOURCE_CLIENT "
            "fra konfigurationen. Der falder aldrig automatisk tilbage til fixture."
        ),
    )
    fixture_revision: int = Field(
        default=1, ge=1, le=2, description="Fixtursæt, kun relevant for source_client=fixture."
    )
    since: date | None = Field(
        default=None, description="Hent kun dokumenter ændret fra og med denne dato."
    )
    limit: int | None = Field(
        default=None, ge=1, le=10000, description="Behandl højst dette antal dokumenter."
    )


class StatsOut(BaseModel):
    """Nøgletal til forsiden og adminvisningen."""

    documents_total: int = 0
    documents_maritime: int = 0
    documents_possible: int = 0
    documents_synthetic: int = 0
    versions_total: int = 0
    categories_total: int = 0
    average_maritime_score: float = 0.0
    documents_by_status: dict[str, int] = Field(default_factory=dict)
    documents_by_type: dict[str, int] = Field(default_factory=dict)
    top_categories: list[CategoryWithCount] = Field(default_factory=list)
    last_import: ImportRunOut | None = None
    source_client: str = "production"
    database_backend: str = "unknown"
    search_backend: str = "unknown"
    legal_notice: str = LEGAL_SOURCE_NOTICE


class HealthOut(BaseModel):
    status: str = "ok"
    database: str = "ok"
    version: str = "1.0.0"

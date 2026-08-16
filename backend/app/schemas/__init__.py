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


class ParagraphOut(BaseModel):
    """En paragraf med sin kapitelkontekst.

    Den primære retrieval-enhed. Et søgeresultat peger på netop den
    bestemmelse, brugeren skal læse — ikke på "et sted i dokumentet".
    """

    paragraph_id: str
    chapter_no: str | None = None
    chapter_title: str | None = None
    section_title: str | None = None
    #: "Kapitel 3 — Skibets drift · § 12". Klar til visning.
    legal_path: str = ""
    #: "Lov om sikkerhed til søs § 12, kapitel 3".
    full_citation: str = ""
    snippet: str = ""


class RankingAdjustmentOut(BaseModel):
    """En domæneregel der ændrede resultatets placering."""

    name: str
    factor: float
    reason: str
    #: Procentvis ændring: -30 betyder "nedjusteret 30 %".
    percent: int = 0


class RankingBreakdownOut(BaseModel):
    """Regnestykket bag et resultats placering.

    Findes i svaret, så brugerfladen kan forklare rækkefølgen frem for at
    præsentere ét uigennemsigtigt tal.
    """

    lexical_score: float = 0.0
    semantic_score: float = 0.0
    authority_score: float = 0.0
    scope_score: float = 0.0
    maritime_score: float = 0.0
    status_score: float = 0.0
    base_score: float = 0.0
    multiplier: float = 1.0
    final_score: float = 0.0
    adjustments: list[RankingAdjustmentOut] = Field(default_factory=list)


class QueryIntentOut(BaseModel):
    """Hvordan søgestrengen blev forstået."""

    kind: str = "broad"
    label: str = ""
    tokens: list[str] = Field(default_factory=list)
    niche_groups: list[str] = Field(default_factory=list)
    #: Læsbare navne til grupperne. Brug disse i brugerfladen.
    niche_labels: list[str] = Field(default_factory=list)
    niche_terms: list[str] = Field(default_factory=list)
    strength: float = 0.0
    #: Sat hvis klassifikationen blev justeret efter delsøgningen.
    refined_from: str | None = None
    refinement_reason: str | None = None


class DocumentSummaryOut(BaseModel):
    """Dokument i lister og søgeresultater.

    Indeholder nok til at forstå resultatet uden at åbne dokumentet.
    """

    id: int
    source_id: str
    retsinformation_id: str | None = None
    document_number: str | None = None
    #: Den juridisk korrekte, fulde titel. Bruges i metadata og citater.
    title: str
    #: Samme værdi som `title` under et navn, der ikke kan misforstås.
    #: Brugerfladen viser `display_title` og gemmer denne bag et fold-ud.
    original_title: str = ""
    #: Kort, læsbar titel. Brug denne overalt i brugerfladen.
    display_title: str = ""
    #: "kernelaw", "speciallaw" eller "support".
    law_class: str | None = None
    law_class_label: str | None = None
    #: 0–1. Hvor bredt reglen gælder.
    scope_score: float = 0.55
    #: 0–1. Vægt som retskilde.
    authority_score: float = 0.5
    #: Nichegrupper titlen peger på: "fiskeskibe", "groenland", ...
    niche_groups: list[str] = Field(default_factory=list)
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
    """Søgeresultat med rangering og tekstuddrag.

    De tre scorer holdes adskilt, så brugerfladen kan vise HVORFOR et
    dokument står hvor det står: fordi ordene stod der, fordi betydningen
    lignede, eller begge dele.
    """

    rank: float = 0.0
    snippet: str = ""
    #: Leksikalsk rang. None hvis dokumentet kun blev fundet semantisk.
    lexical_rank: float | None = None
    #: Cosinus-lighed 0–1 med dokumentets bedst matchende stykke.
    semantic_score: float | None = None
    #: "lexical", "semantic" eller "both".
    match_source: str = "lexical"
    #: Overskrift på det stykke der matchede, f.eks. "§ 12".
    matched_heading: str | None = None
    #: Den bedst matchende paragraf med kapitelkontekst.
    paragraph: ParagraphOut | None = None
    #: Yderligere matchende paragraffer i samme dokument. Brugeren kan
    #: folde dem ud uden at åbne dokumentet.
    paragraphs: list[ParagraphOut] = Field(default_factory=list)
    #: Regnestykket bag placeringen. None ved sortering på dato eller titel.
    ranking: RankingBreakdownOut | None = None


class ChangeLogEntryOut(ORMBase):
    id: int
    change_type: str
    detail: str | None = None
    old_version_id: int | None = None
    new_version_id: int | None = None
    import_run_id: int | None = None
    created_at: datetime


class StructureParagraphOut(BaseModel):
    """En paragraf i dokumentets struktur.

    ``text`` er paragraffens FULDE ordlyd inklusive stykker. Læsevisningen
    sætter lovteksten af disse felter, og et afkortet uddrag ville
    betyde, at brugeren læste en forkortet lovtekst uden at vide det.
    """

    paragraph_id: str
    sort_key: str = ""
    heading: str | None = None
    text: str = ""


class StructureChapterOut(BaseModel):
    """Et kapitel med sine paragraffer."""

    number: str
    title: str | None = None
    section_no: str | None = None
    section_title: str | None = None
    paragraphs: list[StructureParagraphOut] = Field(default_factory=list)


class DocumentStructureOut(BaseModel):
    """Dokumentets juridiske form.

    Gør det muligt for brugerfladen at vise lovteksten som en læsevisning
    med indholdsfortegnelse frem for én lang blok — og at folde præamblen
    sammen, så første skærmbillede viser en regel og ikke en hjemmel.
    """

    has_paragraphs: bool = False
    #: Kundgørelsesformlen. Foldes sammen som standard i brugerfladen.
    #: Brødteksten sendes IKKE med her — den ligger allerede i
    #: `DocumentDetailOut.content`, og paragrafferne nedenfor bærer den
    #: opdelt. Tre kopier af den samme lovtekst i ét svar er spild.
    preamble: str = ""
    chapters: list[StructureChapterOut] = Field(default_factory=list)
    #: Paragraffer uden kapitel — små bekendtgørelser har ingen kapitler.
    loose_paragraphs: list[StructureParagraphOut] = Field(default_factory=list)
    paragraph_count: int = 0


class DocumentDetailOut(DocumentSummaryOut):
    """Fuldt dokument til detaljevisning."""

    source: str
    content: str = ""
    #: Dokumentets juridiske struktur. Tom hvis teksten ikke kunne parses.
    structure: DocumentStructureOut = Field(default_factory=DocumentStructureOut)
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


class RelatedQueryOut(BaseModel):
    """En tidligere søgning der ligner den aktuelle."""

    query: str
    similarity: float
    occurrences: int
    last_result_count: int


class LoggedQueryOut(BaseModel):
    """En post i søgeloggen."""

    id: int
    query: str
    occurrences: int
    last_result_count: int
    best_result_count: int
    last_mode: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class SimilarDocumentOut(DocumentSummaryOut):
    """Et dokument der ligner et andet, med begrundelse."""

    similarity: float
    matched_heading: str | None = None
    excerpt: str = ""


class EmbeddingStatusOut(BaseModel):
    """Tilstanden for det semantiske indeks. Vises i driftsvisningen."""

    enabled: bool = False
    provider: str | None = None
    model: str | None = None
    dimensions: int | None = None
    #: Falsk for hash-udbyderen. Brugerfladen må ikke kalde det
    #: "betydningssøgning", hvis vektorerne ikke bærer betydning.
    semantic: bool = False
    pgvector: bool = False
    maritime_documents: int = 0
    embedded_documents: int = 0
    pending_documents: int = 0
    chunks: int = 0
    chunks_from_other_model: int = 0
    coverage_pct: float = 0.0
    #: Sat når status ikke kunne beregnes — f.eks. model ikke indlæst.
    error: str | None = None


class EmbeddingRunRequest(BaseModel):
    """Anmodning om at vektorisere det der mangler."""

    #: Antal dokumenter i denne kørsel. Holdes lavt som standard, fordi
    #: kaldet er synkront og ellers ville løbe ind i en HTTP-timeout.
    #: Kør hele indekset fra kommandolinjen: `python -m app.cli embed run`.
    limit: int = Field(default=200, ge=1, le=5000)
    #: Vektorisér også ikke-maritime dokumenter. Sjældent nyttigt.
    include_non_maritime: bool = False
    #: Slet alle vektorer og byg forfra. Nødvendigt ved modelskifte.
    reset: bool = False


class EmbeddingRunOut(BaseModel):
    """Resultatet af en vektoriseringskørsel."""

    documents_checked: int = 0
    documents_embedded: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    chunks_written: int = 0
    chunks_deleted: int = 0
    model: str = ""
    errors: list[str] = Field(default_factory=list)
    #: Hvad der stadig mangler efter kørslen.
    pending_after: int = 0


class SearchResponse(BaseModel):
    """Søgesvar med resultater og anvendte filtre."""

    items: list[SearchHitOut]
    total: int
    page: int
    page_size: int
    total_pages: int
    query: str | None = None
    backend: str = "fallback"
    #: Den tilstand der faktisk blev brugt: lexical | semantic | hybrid.
    #: Kan afvige fra den ønskede — se `notice`.
    mode: str = "lexical"
    #: Var der vektorer at søge i?
    semantic_available: bool = False
    #: Sandt når `total` er et undertal, fordi kandidatloftet blev ramt.
    truncated: bool = False
    #: Forklaring, hvis den ønskede tilstand ikke kunne leveres.
    notice: str | None = None
    #: Hvordan søgestrengen blev forstået — bred, semispecifik eller niche.
    #: Afgør domænereglerne, og gør en uventet rækkefølge forklarlig.
    intent: QueryIntentOut | None = None
    #: Tidligere søgninger der ligner denne. Tom uden søgelog.
    related_queries: list[RelatedQueryOut] = Field(default_factory=list)
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    legal_notice: str = LEGAL_SOURCE_NOTICE


class FacetValue(BaseModel):
    value: str
    count: int


class LawClassFacet(BaseModel):
    """En dokumentklasse som filtervalg, med forklaring."""

    value: str
    label: str
    description: str = ""
    count: int = 0


class FacetsOut(BaseModel):
    """Tilgængelige filterværdier, så brugerfladen ikke hardcoder dem."""

    document_types: list[FacetValue] = Field(default_factory=list)
    authorities: list[FacetValue] = Field(default_factory=list)
    statuses: list[FacetValue] = Field(default_factory=list)
    categories: list[CategoryWithCount] = Field(default_factory=list)
    law_classes: list[LawClassFacet] = Field(default_factory=list)


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
    #: Tilstanden for det semantiske indeks.
    embeddings: EmbeddingStatusOut = Field(default_factory=EmbeddingStatusOut)
    #: Nøgletal fra søgeloggen: distinct_queries, total_searches,
    #: queries_without_results, vectorized_queries.
    search_log: dict[str, int] = Field(default_factory=dict)
    legal_notice: str = LEGAL_SOURCE_NOTICE


class AdminSessionOut(BaseModel):
    """Svar på "er dette token gyldigt?".

    Brugerfladen kalder endepunktet, før den viser driftssiden, så et
    forkert token giver en forståelig besked frem for seks mislykkede
    kald. Svaret indeholder bevidst ingen hemmeligheder.
    """

    authenticated: Literal[True] = True
    environment: str = "development"
    app_name: str = "Maritim Lovdatabase"


class HealthOut(BaseModel):
    status: str = "ok"
    database: str = "ok"
    version: str = "1.0.0"

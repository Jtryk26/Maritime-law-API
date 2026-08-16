"""Applikationskonfiguration.

Alle miljøafhængige værdier læses herfra. Ingen hardcodede stier,
URL'er eller hemmeligheder andre steder i koden.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> repo-rod
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime-indstillinger, læst fra miljøvariabler eller .env."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Applikation --------------------------------------------------------
    app_name: str = "Maritim Lovdatabase"
    environment: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api"

    #: Skal /docs, /redoc og /openapi.json udstilles? Skemaet afslører hele
    #: driftsgrænsefladen. Slå det fra, når tjenesten er offentligt tilgængelig.
    expose_api_docs: bool = True

    # --- Administratoradgang ------------------------------------------------
    # Alle skrive- og driftsendepunkter kræver dette token som
    # `Authorization: Bearer <token>`. Der findes bevidst ingen brugerdatabase:
    # systemet har én driftsansvarlig, og et delt token kan udskiftes ved at
    # ændre én miljøvariabel og genstarte.
    #
    # Er tokenet ikke sat, svarer driftsendepunkterne 503 — de er altså
    # lukkede som udgangspunkt, ikke åbne. I produktion nægter applikationen
    # helt at starte uden token.
    admin_api_token: str | None = None
    #: Kortere tokens afvises. 32 tegn fra `secrets.token_urlsafe(32)` er
    #: en fornuftig standard.
    admin_token_min_length: int = 24

    # --- Rate limiting (indgående) ------------------------------------------
    # Beskytter den offentlige søgning. Grænserne er pr. klient-IP og pr.
    # proces; systemet kører i én backend-container, så det er tilstrækkeligt.
    # nginx håndhæver de samme grænser foran applikationen, så et angreb
    # standses før det koster en Python-forespørgsel.
    rate_limit_enabled: bool = True
    #: Almindelige API-kald (dokumenter, kategorier, facetter).
    rate_limit_requests_per_minute: int = 120
    #: Søgning er dyrere — både leksikalsk og semantisk — og har egen grænse.
    rate_limit_search_per_minute: int = 30
    #: Loft for hvor mange klienter der huskes. Beskytter mod at et
    #: distribueret angreb spiser hukommelse via selve tælleren.
    rate_limit_max_tracked_clients: int = 20000

    #: Stol på CF-Connecting-IP / X-Forwarded-For ved bestemmelse af klientens
    #: adresse. SKAL kun være sand, når applikationen står bag en proxy man
    #: kontrollerer (nginx, cloudflared). Ellers kan enhver klient forfalske
    #: sin egen adresse og dermed omgå rate limiting.
    trust_proxy_headers: bool = False

    # --- Database -----------------------------------------------------------
    # PostgreSQL i produktion. SQLite understøttes til lokal udvikling/test.
    database_url: str = Field(
        default=f"sqlite:///{REPO_ROOT / 'data' / 'maritime.db'}",
    )
    db_echo: bool = False

    # Kør Alembic-migrationer ved opstart (bekvemt i Docker).
    run_migrations_on_startup: bool = True

    # --- Retsinformation ----------------------------------------------------
    # Officiel høsteservice. Se README for verificeret dokumentation.
    retsinformation_base_url: str = "https://api.retsinformation.dk"
    retsinformation_document_base_url: str = "https://www.retsinformation.dk"
    retsinformation_timeout_seconds: float = 30.0
    retsinformation_max_retries: int = 3
    # Officiel begrænsning: højst 1 kald pr. 10 sekunder.
    retsinformation_min_request_interval_seconds: float = 10.0
    retsinformation_user_agent: str = "maritim-lovdatabase/1.0 (+lokal indeksering)"

    # --- Retsinformation: søgning (opdagelse) -------------------------------
    # Høsteservicen kan ikke liste lovsamlingen. Kandidat-accessionsnumre
    # findes via søgesiden på www.retsinformation.dk, hvis dataendpoint IKKE
    # er en dokumenteret grænseflade. URL'en er derfor bevidst tom som
    # standard: den skal aflæses i browserens netværksfane og sættes her.
    # En gættet URL ville ligne en færdig integration og fejle stille.
    # Kontrollér med: python -m app.cli backfill probe-search
    retsinformation_search_url: str | None = None
    retsinformation_search_method: str = "GET"
    #: Anmodningens parametre som JSON. Pladsholdere: {authority}, {status},
    #: {page}, {page_size}, {offset}.
    retsinformation_search_params: str = ""
    retsinformation_search_page_size: int = 100
    #: "page" (sidetal) eller "offset" (springtal).
    retsinformation_search_pagination: str = "page"
    #: Er første side 0 eller 1? Aflæses sammen med parametrene.
    retsinformation_search_first_page: int = 1
    #: Loft mod uendelige løkker ved uventet paginering.
    retsinformation_search_max_pages: int = 200
    #: Søgesiden har ingen publiceret grænse; vi begrænser os selv alligevel.
    retsinformation_search_min_request_interval_seconds: float = 2.0

    # Normal drift bruger altid Retsinformations officielle høsteservice.
    # Fixture kan fortsat vælges eksplicit i automatiske tests.
    source_client: str = "production"

    # --- Konfigurationsfiler ------------------------------------------------
    config_dir: Path = REPO_ROOT / "config"
    fixture_dir: Path = REPO_ROOT / "data" / "fixtures"
    #: CSV-manifester fra `backfill discover` lander her.
    manifest_dir: Path = REPO_ROOT / "manifests"

    # --- Semantisk søgning (vektorer) ---------------------------------------
    # Slår hele vektorlaget til. Er den falsk, opfører systemet sig
    # nøjagtigt som før: leksikalsk søgning alene, ingen embedding-model
    # indlæses, ingen chunks skrives.
    embeddings_enabled: bool = True
    #: "local" (sentence-transformers i containeren), "api" (OpenAI-kompatibelt
    #: endpoint) eller "hashing" (deterministisk, IKKE semantisk — kun test).
    #: Der falder aldrig automatisk tilbage til en anden udbyder; en
    #: utilgængelig model er en fejl, ikke en stille forringelse.
    embedding_provider: str = "local"
    #: Modelnavn. For "local" er det et sentence-transformers-id.
    #: multilingual-e5-small er valgt fordi den er flersproget (dansk
    #: indgår i træningsdata), kun 384 dimensioner og kører på CPU.
    embedding_model: str = "intfloat/multilingual-e5-small"
    #: Vektorlængde. SKAL stemme med modellen — se `cli embed model-info`.
    #: Ændres den, skal pgvector-kolonnen genskabes: `cli embed vector-column`.
    embedding_dimensions: int = 384
    #: E5-modeller er trænet med asymmetriske præfikser. Uden dem falder
    #: kvaliteten mærkbart. Tomme strenge for modeller uden præfikskrav.
    embedding_query_prefix: str = "query: "
    embedding_passage_prefix: str = "passage: "
    embedding_batch_size: int = 16
    #: Antal tråde til CPU-inferens. 0 = lad torch bestemme.
    embedding_torch_threads: int = 0

    # Kun for embedding_provider="api".
    embedding_api_url: str | None = None
    embedding_api_key: str | None = None
    embedding_api_timeout_seconds: float = 30.0
    embedding_api_max_retries: int = 3

    # --- Chunking -----------------------------------------------------------
    # Lovtekster er for lange til én vektor. De deles ved paragraf- og
    # afsnitsgrænser, så et chunk så vidt muligt er én bestemmelse.
    chunk_target_chars: int = 1200
    chunk_max_chars: int = 2000
    chunk_overlap_chars: int = 150
    chunk_min_chars: int = 120
    #: Loft pr. dokument. Beskytter mod at én kæmpelov fylder indekset.
    chunk_max_per_document: int = 400

    # --- Vektorsøgning ------------------------------------------------------
    #: Standardtilstand for /api/search: "lexical", "semantic" eller "hybrid".
    #: Falder automatisk til "lexical", hvis der ikke findes vektorer endnu.
    search_default_mode: str = "hybrid"
    #: Antal kandidater der hentes fra hver delsøgning før sammensmeltning.
    hybrid_candidate_limit: int = 200
    #: RRF-konstanten. 60 er den værdi der bruges i litteraturen.
    hybrid_rrf_k: int = 60
    #: Vægte i sammensmeltningen. Leksikalsk vejer lidt tungest, fordi
    #: juridisk søgning ofte er efter en bestemt term, ikke et tema.
    hybrid_lexical_weight: float = 1.0
    hybrid_semantic_weight: float = 0.8
    #: Nedre grænse for hvad der tælles som et semantisk hit.
    #: None (standard) betyder "brug udbyderens eget forslag" — se
    #: `ProviderInfo.suggested_min_similarity`. Skalaen afhænger af
    #: modellen, og udbyderen er det eneste sted der ved noget om den.
    #: Sæt en værdi her, når du har målt på netop din model.
    vector_min_similarity: float | None = None
    #: Loft for den portable brute force-søgning (SQLite, eller PostgreSQL
    #: uden pgvector). Over dette antal er svartiden ikke forsvarlig.
    vector_fallback_max_chunks: int = 20000

    # --- Søgelog ------------------------------------------------------------
    #: Log og vektorisér de søgninger der faktisk stilles. Grundlaget for
    #: "relaterede søgninger" og for senere spørgsmål/svar-funktioner.
    search_query_log_enabled: bool = True
    #: Søgninger kortere end dette logges ikke (typisk halvskrevne ord).
    search_query_log_min_chars: int = 2
    #: Mindste lighed før to søgninger regnes som beslægtede.
    related_query_min_similarity: float = 0.55

    # --- Import -------------------------------------------------------------
    # Dokumenter med maritim score under denne værdi gemmes ikke lokalt.
    # Sat til "possible"-tærsklen, så grænsetilfælde stadig kan inspiceres.
    import_store_min_score: int = 30
    # Antal på hinanden følgende fejl før importen afbrydes som usund.
    import_max_consecutive_failures: int = 25

    # --- CORS ---------------------------------------------------------------
    # I den udrullede opsætning serverer nginx både frontend og /api fra
    # samme oprindelse; da er CORS unødvendigt og listen kan være tom.
    # Værdierne herunder gælder udvikling, hvor Vite kører på 5173.
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://localhost:8080"

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("admin_api_token")
    @classmethod
    def _blank_token_is_none(cls, value: str | None) -> str | None:
        """En tom miljøvariabel skal betyde "ikke sat", ikke "tomt token"."""
        if value is None:
            return None
        value = value.strip()
        return value or None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def maritime_keywords_path(self) -> Path:
        return self.config_dir / "maritime_keywords.yaml"

    @property
    def categories_path(self) -> Path:
        return self.config_dir / "categories.yaml"

    @property
    def discovery_global_config_path(self) -> Path:
        return self.config_dir / "discovery_global.yaml"

    @property
    def embedding_dir(self) -> Path:
        """Cache for lokalt hentede modeller. Sat i Docker, så modellen
        bages ind i imaget og ikke hentes ved hver opstart."""
        return Path(os.environ.get("SENTENCE_TRANSFORMERS_HOME", str(REPO_ROOT / "data" / "models")))

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql", "postgres://"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cachet settings-instans."""
    return Settings()

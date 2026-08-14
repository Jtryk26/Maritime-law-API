"""Applikationskonfiguration.

Alle miljøafhængige værdier læses herfra. Ingen hardcodede stier,
URL'er eller hemmeligheder andre steder i koden.
"""

from __future__ import annotations

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

    # --- Import -------------------------------------------------------------
    # Dokumenter med maritim score under denne værdi gemmes ikke lokalt.
    # Sat til "possible"-tærsklen, så grænsetilfælde stadig kan inspiceres.
    import_store_min_score: int = 30
    # Antal på hinanden følgende fejl før importen afbrydes som usund.
    import_max_consecutive_failures: int = 25

    # --- CORS ---------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://localhost:8080"

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

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
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql", "postgres://"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cachet settings-instans."""
    return Settings()

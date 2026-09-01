"""FastAPI-applikationen.

Startes med::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import RateLimitMiddleware
from app.api.routes import create_api_router
from app.core.config import Settings, get_settings, validate_production_invariants
from app.core.logging import configure_logging, get_logger
from app.core.security import verify_admin_auth
from app.db.migrations_runner import run_migrations
from app.db.seed import seed_categories
from app.db.session import get_engine, session_scope
from app.schemas import HealthOut

DESCRIPTION = """
Lokal, søgbar database over **dansk maritim lovgivning**.

Dokumenter høstes fra Retsinformation, normaliseres, vurderes for maritim
relevans, kategoriseres i en maritim taksonomi og versioneres lokalt.

Retsinformation er fortsat den officielle retskilde. Denne tjeneste er et
søge- og analyseværktøj og erstatter ikke den officielle kundgørelse.
"""


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    """Applikationsfabrik.

    Gør det muligt at instantiere applikationen med specifikke konfigurationer,
    f.eks. ren offentlig læsetilstand (ENABLE_ADMIN_API=false) eller testmiljø.
    """
    settings = custom_settings or get_settings()
    validate_production_invariants(settings)
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """Klargør databasen ved opstart."""
        verify_admin_auth(settings)

        if settings.run_migrations_on_startup:
            run_migrations()

        with session_scope() as session:
            seed_categories(session)

        logger.info(
            "app.started",
            extra={
                "environment": settings.environment,
                "database": get_engine().dialect.name,
                "source_client": settings.source_client,
                "admin_api_enabled": settings.enable_admin_api,
            },
        )
        yield
        get_engine().dispose()
        logger.info("app.stopped")

    application = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.expose_api_docs else None,
        redoc_url="/redoc" if settings.expose_api_docs else None,
        openapi_url="/openapi.json" if settings.expose_api_docs else None,
        openapi_tags=[
            {"name": "søgning", "description": "Fritekstsøgning og filtrering."},
            {"name": "dokumenter", "description": "Dokumenter, versioner og forklaringer."},
            {"name": "kategorier", "description": "Den maritime taksonomi."},
            {"name": "anvendelighed", "description": "Vurdering af regelanvendelighed."},
            {"name": "drift", "description": "Nøgletal og systemtilstand."},
        ],
    )

    application.add_middleware(RateLimitMiddleware, settings=settings)

    cors_origins = settings.cors_origin_list
    if "*" in cors_origins and settings.is_production:
        raise RuntimeError(
            "CORS_ORIGINS=* er ikke tilladt i produktion. Angiv de konkrete "
            "oprindelser, eller lad værdien være tom, når frontend og API "
            "serveres fra samme domæne."
        )
    if cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
    else:
        logger.info("app.cors.disabled")

    register_exception_handlers(application)
    application.include_router(
        create_api_router(settings.enable_admin_api), prefix=settings.api_prefix
    )

    @application.get("/health", response_model=HealthOut, tags=["drift"], summary="Systemtilstand")
    def health() -> HealthOut:
        """Bruges af Docker healthcheck og overvågning."""
        database = "ok"
        try:
            with session_scope() as session:
                session.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover
            logger.error("health.database.failed", extra={"error": str(exc)})
            database = "fejl"
        return HealthOut(
            status="ok" if database == "ok" else "degraded",
            database=database,
            version=__version__,
        )

    return application


app = create_app()

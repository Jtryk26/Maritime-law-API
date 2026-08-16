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
from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import verify_admin_auth
from app.db.migrations_runner import run_migrations
from app.db.seed import seed_categories
from app.db.session import get_engine, session_scope
from app.schemas import HealthOut

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)

DESCRIPTION = """
Lokal, søgbar database over **dansk maritim lovgivning**.

Dokumenter høstes fra Retsinformation, normaliseres, vurderes for maritim
relevans, kategoriseres i en maritim taksonomi og versioneres lokalt.

Retsinformation er fortsat den officielle retskilde. Denne tjeneste er et
søge- og analyseværktøj og erstatter ikke den officielle kundgørelse.
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Klargør databasen ved opstart."""
    # Kontrolleres først: en tjeneste, der ikke kan beskytte sine
    # driftsendepunkter, skal ikke nå at binde en port.
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
        },
    )
    yield
    get_engine().dispose()
    logger.info("app.stopped")


app = FastAPI(
    title=settings.app_name,
    description=DESCRIPTION,
    version=__version__,
    lifespan=lifespan,
    # OpenAPI-skemaet beskriver hele driftsgrænsefladen. Når tjenesten er
    # offentligt tilgængelig, sættes EXPOSE_API_DOCS=false, og /docs,
    # /redoc og /openapi.json forsvinder.
    docs_url="/docs" if settings.expose_api_docs else None,
    redoc_url="/redoc" if settings.expose_api_docs else None,
    openapi_url="/openapi.json" if settings.expose_api_docs else None,
    openapi_tags=[
        {"name": "søgning", "description": "Fritekstsøgning og filtrering."},
        {"name": "dokumenter", "description": "Dokumenter, versioner og forklaringer."},
        {"name": "kategorier", "description": "Den maritime taksonomi."},
        {"name": "import", "description": "Kørsel og historik for import."},
        {"name": "drift", "description": "Nøgletal og systemtilstand."},
    ],
)

# Rækkefølgen er vigtig: middleware tilføjet sidst kører først. Rate
# limiting skal ligge yderst, så en afvist forespørgsel ikke når hverken
# CORS-behandling eller ruter.
app.add_middleware(RateLimitMiddleware, settings=settings)

# I den udrullede opsætning serverer nginx frontend og API fra samme
# oprindelse. Da er CORS unødvendigt, og en tom liste betyder, at browsere
# ikke får lov at kalde API'et fra andre websteder overhovedet.
_cors_origins = settings.cors_origin_list
if "*" in _cors_origins and settings.is_production:
    raise RuntimeError(
        "CORS_ORIGINS=* er ikke tilladt i produktion. Angiv de konkrete "
        "oprindelser, eller lad værdien være tom, når frontend og API "
        "serveres fra samme domæne."
    )
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        # Administratortokenet sendes i Authorization-headeren; den skal
        # være tilladt, når frontend kører på en anden oprindelse (Vite).
        allow_headers=["Authorization", "Content-Type"],
    )
else:
    logger.info("app.cors.disabled")

register_exception_handlers(app)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/health", response_model=HealthOut, tags=["drift"], summary="Systemtilstand")
def health() -> HealthOut:
    """Bruges af Docker healthcheck og overvågning."""
    database = "ok"
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        logger.error("health.database.failed", extra={"error": str(exc)})
        database = "fejl"
    return HealthOut(status="ok" if database == "ok" else "degraded",
                     database=database, version=__version__)

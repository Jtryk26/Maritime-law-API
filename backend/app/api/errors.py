"""Ensartet fejlhåndtering for API'et."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.services.retsinformation.base import (
    DocumentNotFoundError,
    PermanentSourceError,
    TransientSourceError,
)
from app.services.retsinformation.factory import UnknownSourceClientError

logger = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Tilknytter fejlhåndtering, så klienter altid får samme format."""

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "error_type": "http_error"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Ugyldige forespørgselsparametre.",
                "error_type": "validation_error",
                "errors": exc.errors(),
            },
        )

    @app.exception_handler(UnknownSourceClientError)
    async def unknown_client(_: Request, exc: UnknownSourceClientError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_type": "unknown_source_client"},
        )

    @app.exception_handler(DocumentNotFoundError)
    async def source_not_found(_: Request, exc: DocumentNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "error_type": "source_document_not_found"},
        )

    @app.exception_handler(TransientSourceError)
    async def source_unavailable(_: Request, exc: TransientSourceError) -> JSONResponse:
        # 503: kilden er midlertidigt utilgængelig, forsøg igen senere.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": f"Retsinformation er midlertidigt utilgængelig: {exc}",
                "error_type": "source_unavailable",
            },
        )

    @app.exception_handler(PermanentSourceError)
    async def source_error(_: Request, exc: PermanentSourceError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "detail": f"Fejl i svaret fra Retsinformation: {exc}",
                "error_type": "source_error",
            },
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "api.unhandled_error",
            extra={"path": request.url.path, "error_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Der opstod en intern fejl. Se serverloggen for detaljer.",
                "error_type": "internal_error",
            },
        )

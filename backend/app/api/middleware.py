"""HTTP-middleware: klientidentifikation og rate limiting.

Grænserne håndhæves også i nginx foran applikationen. Det er ikke
dobbeltarbejde: nginx afviser et angreb, før det koster en Python-
forespørgsel, mens denne middleware sikrer, at grænsen også gælder, hvis
API'et nås direkte — fra det lokale netværk, fra en anden container eller
under udvikling.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.request_limits import SlidingWindowLimiter

logger = get_logger(__name__)

__all__ = ["RateLimitMiddleware", "client_address"]

#: Cloudflare sætter denne header med den oprindelige klients adresse.
#: Den er kun troværdig, når trafikken faktisk kommer gennem vores egen
#: tunnel — derfor `trust_proxy_headers`.
_CLOUDFLARE_HEADER = "cf-connecting-ip"
_FORWARDED_HEADER = "x-forwarded-for"

_UNKNOWN_CLIENT = "ukendt"


def client_address(request: Request, *, trust_proxy_headers: bool) -> str:
    """Klientens adresse, som den skal bruges til rate limiting.

    Uden `trust_proxy_headers` bruges udelukkende socket-adressen. Ville
    vi stole på headere fra en vilkårlig afsender, kunne enhver klient
    skrive sin egen `X-Forwarded-For` og få en frisk kvote for hver
    forespørgsel — altså ingen rate limiting overhovedet.
    """
    if trust_proxy_headers:
        cloudflare = request.headers.get(_CLOUDFLARE_HEADER)
        if cloudflare:
            return cloudflare.strip()

        forwarded = request.headers.get(_FORWARDED_HEADER)
        if forwarded:
            # Første led er den oprindelige klient; resten er proxykæden.
            first = forwarded.split(",")[0].strip()
            if first:
                return first

    client = request.client
    return client.host if client else _UNKNOWN_CLIENT


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Pr. IP-grænse på det offentlige API.

    To kvoter, fordi de to slags kald ikke koster det samme: en søgning
    rammer både fuldtekstindekset og — i hybridtilstand — embedding-
    modellen, mens et opslag på et dokument er én indekseret læsning.
    """

    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings
        self._api_prefix = settings.api_prefix.rstrip("/")
        self._search_prefix = f"{self._api_prefix}/search"
        window = 60.0
        self._general = SlidingWindowLimiter(
            settings.rate_limit_requests_per_minute,
            window,
            max_keys=settings.rate_limit_max_tracked_clients,
        )
        self._search = SlidingWindowLimiter(
            settings.rate_limit_search_per_minute,
            window,
            max_keys=settings.rate_limit_max_tracked_clients,
        )

    def _limiter_for(self, path: str) -> tuple[SlidingWindowLimiter, str] | None:
        """Vælger kvote ud fra stien. `None` = ingen begrænsning."""
        if not path.startswith(f"{self._api_prefix}/"):
            # /health skal kunne kaldes af Docker og overvågning uden loft.
            return None
        if path.startswith(self._search_prefix):
            return self._search, "search"
        return self._general, "api"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self._settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)

        selected = self._limiter_for(request.url.path)
        if selected is None:
            return await call_next(request)

        limiter, bucket = selected
        key = f"{bucket}:{client_address(request, trust_proxy_headers=self._settings.trust_proxy_headers)}"
        decision = limiter.check(key)

        if not decision.allowed:
            logger.info(
                "api.rate_limited",
                extra={"bucket": bucket, "path": request.url.path, "limit": decision.limit},
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": (
                        "For mange forespørgsler. Prøv igen om "
                        f"{decision.retry_after} sekunder."
                    ),
                    "error_type": "rate_limited",
                },
                headers={
                    "Retry-After": str(decision.retry_after),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response

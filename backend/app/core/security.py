"""Adgangskontrol til skrive- og driftsgrænseflader.

Systemet har to slags brugere, og kun to:

* **Den offentlige læser** — en søgende, der må læse lovtekst, metadata,
  versioner og kategorier. Ingen legitimation.
* **Den driftsansvarlige** — den ene person, der starter importer,
  bygger vektorindekset og ser driftstal og søgelog.

Derfor er der ikke en brugerdatabase, men ét delt token. Et rollesystem
med brugere, kodeord og sessioner ville koste vedligehold uden at give
mere sikkerhed i en installation med én administrator. Skulle der senere
komme flere roller, er `require_admin` det eneste sted der skal ændres —
ruterne kender kun dependencyen.

Designvalg:

* **Lukket som udgangspunkt.** Er `ADMIN_API_TOKEN` ikke sat, svarer
  driftsendepunkterne 503. En glemt konfiguration lader dem altså ikke
  stå åbne.
* **Nægter at starte i produktion uden token.** Se `verify_admin_auth`.
* **Sammenligning i konstant tid** med `secrets.compare_digest`.
* **Ingen tokenværdi i loggen.** Kun at et forsøg blev afvist.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["AdminAuth", "require_admin", "verify_admin_auth"]

#: `auto_error=False`, så vi selv kan skelne mellem "ikke konfigureret"
#: (503) og "manglende eller forkert token" (401).
_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="Administratortoken",
    description=(
        "Sendes som `Authorization: Bearer <token>`. Værdien er "
        "`ADMIN_API_TOKEN` fra serverens miljø."
    ),
)

_UNAUTHENTICATED_HEADERS = {"WWW-Authenticate": 'Bearer realm="drift"'}


class AdminAuthNotConfiguredError(RuntimeError):
    """Rejses ved opstart, hvis produktion mangler et brugbart token."""


def verify_admin_auth(settings: Settings | None = None) -> None:
    """Kontrollerer administratorkonfigurationen ved opstart.

    I produktion er et manglende eller for kort token en opstartsfejl.
    Alternativet — at starte alligevel og lade endepunkterne svare 503 —
    ville give en tjeneste, der ser rask ud, men ikke kan drives.

    Uden for produktion er det en advarsel, så testsuiten og en frisk
    udviklingsmaskine kan køre uden opsætning.
    """
    settings = settings or get_settings()
    token = settings.admin_api_token

    if not token:
        if settings.is_production:
            raise AdminAuthNotConfiguredError(
                "ADMIN_API_TOKEN er ikke sat. Drifts- og skriveendepunkterne "
                "kan ikke beskyttes, og applikationen nægter at starte i "
                "produktion. Generér et token med: "
                "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        logger.warning(
            "admin.token.missing",
            extra={"detail": "Driftsendepunkterne svarer 503 indtil ADMIN_API_TOKEN er sat."},
        )
        return

    if len(token) < settings.admin_token_min_length:
        message = (
            f"ADMIN_API_TOKEN er kortere end {settings.admin_token_min_length} tegn "
            "og kan gættes. Generér et nyt med "
            "secrets.token_urlsafe(32)."
        )
        if settings.is_production:
            raise AdminAuthNotConfiguredError(message)
        logger.warning("admin.token.weak", extra={"detail": message})


def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """FastAPI-dependency: kræver et gyldigt administratortoken."""
    settings = get_settings()
    expected = settings.admin_api_token

    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Administratoradgang er ikke konfigureret på denne server. "
                "Sæt ADMIN_API_TOKEN og genstart."
            ),
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Denne grænseflade kræver et administratortoken.",
            headers=_UNAUTHENTICATED_HEADERS,
        )

    if not secrets.compare_digest(credentials.credentials, expected):
        # Tokenet logges aldrig — kun at et forsøg blev afvist.
        logger.warning("admin.token.rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ugyldigt administratortoken.",
            headers=_UNAUTHENTICATED_HEADERS,
        )


#: Bruges som `dependencies=[AdminAuth]` på ruter og routere.
AdminAuth = Depends(require_admin)

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

import hashlib
import secrets
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "AdminAuth",
    "ReviewerContext",
    "hash_reviewer_token",
    "require_admin",
    "require_reviewer",
    "require_reviewer_role",
    "verify_admin_auth",
]

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

    Er administrative endepunkter deaktiveret (ENABLE_ADMIN_API=false), kræves intet token.
    I produktion med aktiveret administrations-API er et manglende eller for kort token en opstartsfejl.
    """
    settings = settings or get_settings()
    if not settings.enable_admin_api:
        logger.info(
            "admin.api.disabled",
            extra={"detail": "Administrative ruter er deaktiveret (ren offentlig læsetilstand)."},
        )
        return

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


# ---------------------------------------------------------------------------
# Navngivne anmeldere (Vessel Applicability 2.0)
# ---------------------------------------------------------------------------
#
# Det delte administratortoken siger "en driftsansvarlig". En godkendelse af
# en juridisk regel skal sige "hvilken person". Derfor har reglernes udkast-
# og godkendelsesruter deres egen adgangskontrol: et personligt token, hvis
# SHA-256 står i REVIEWER_TOKENS sammen med identitet og roller.
#
# Identiteten kommer fra tokenet og ALDRIG fra forespørgslens krop. Et felt
# som "reviewer_id": "jacob" i JSON beviser intet; besiddelse af Jacobs token
# gør.

Role = Literal["drafter", "approver"]

_REVIEWER_HEADERS = {"WWW-Authenticate": 'Bearer realm="anmelder"'}


@dataclass(frozen=True, slots=True)
class ReviewerContext:
    """En verificeret anmelder. Kan kun dannes af `require_reviewer`."""

    reviewer_id: str
    roles: frozenset[str]

    def has(self, role: Role) -> bool:
        return role in self.roles


def hash_reviewer_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_reviewer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> ReviewerContext:
    """FastAPI-dependency: et gyldigt, personligt anmeldertoken.

    Lukket som udgangspunkt: uden konfigurerede anmeldere svarer ruterne 503.
    """
    grants = get_settings().reviewer_tokens
    if not grants:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Ingen anmeldere er konfigureret på denne server. Sæt REVIEWER_TOKENS "
                "(se python -m app.cli reviewer-token) og genstart."
            ),
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Denne grænseflade kræver et personligt anmeldertoken.",
            headers=_REVIEWER_HEADERS,
        )

    presented = hash_reviewer_token(credentials.credentials)
    match = None
    # Alle nøgler sammenlignes, også efter et træf, så svartiden ikke
    # afslører, hvor i listen et token står.
    for key, grant in grants.items():
        if secrets.compare_digest(presented, key):
            match = grant
    if match is None:
        logger.warning("reviewer.token.rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ukendt anmeldertoken.",
            headers=_REVIEWER_HEADERS,
        )
    return ReviewerContext(reviewer_id=match.reviewer_id, roles=frozenset(match.roles))


def require_reviewer_role(role: Role):
    """Dependency-fabrik: anmelderen skal have rollen, ellers 403."""

    def dependency(
        reviewer: Annotated[ReviewerContext, Depends(require_reviewer)],
    ) -> ReviewerContext:
        if not reviewer.has(role):
            logger.warning(
                "reviewer.role.denied", extra={"reviewer": reviewer.reviewer_id, "role": role}
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Anmelderen '{reviewer.reviewer_id}' har ikke rollen '{role}'.",
            )
        return reviewer

    return dependency

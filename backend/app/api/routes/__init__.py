"""Samling af API'ets ruter.

Opdelingen i offentlige og beskyttede routere er en sikkerhedsgrænse, ikke kun
en filopdeling:

* `documents` er den offentlige læsegrænseflade — søgning, dokumenter,
  versioner, kategorier og facetter.
* `applicability` er den offentlige anvendelighedsvurdering. Den ser kun
  regler, et menneske har godkendt. Udkast og gennemgang af regler findes
  **kun** som `python -m app.cli applicability draft|review` i
  drift-containeren — der er ingen HTTP-vej til at ændre en regel.
* `geography` er eksperimentel og findes kun, når GEOGRAPHY_ENABLED=true.
  Offentlig og læsende (områder, kortdata, gennemgåede tilknytninger og et
  separat endepunkt for automatiske fund). Dens `admin_router` (genanalyse)
  er beskyttet som `imports`.
  Gennemgang af fund sker kun via ``python -m app.cli geography review``.
* `imports` er driftsgrænsefladen — importkørsler, vektorisering, nøgletal
  og importhistorik.
* `vessel_applicability` er Vessel Applicability 2.0 under `/v1`. Den er
  udelukkende offentlig og læsende. Regler udkastes, godkendes og udfases
  **kun** via `python -m app.cli vessel-rules` i drift-containeren — der
  findes bevidst ingen HTTP-vej til at ændre dem.

`imports` får administratorkravet på **routerniveau**, så en ny rute i de
filer er beskyttet med det samme. Det modsatte — at huske en dependency pr.
rute — er den fejl, der før eller siden bliver begået.
"""

from fastapi import APIRouter

from app.core.security import AdminAuth

from . import applicability, documents, geography, imports, vessel_applicability

ADMIN_RESPONSES = {
    401: {"description": "Manglende eller ugyldigt administratortoken."},
    503: {"description": "Administratoradgang er ikke konfigureret på serveren."},
}

def create_api_router(
    enable_admin_api: bool = True,
    international_catalogue_enabled: bool = False,
    geography_enabled: bool = False,
) -> APIRouter:
    """Opretter API-routeren.

    Når `enable_admin_api` er False (f.eks. i ren offentlig produktion),
    udelades administrative og skrivende ruter fuldstændigt fra applikationen.
    """
    router = APIRouter()

    # Offentligt (Læsning og regelanvendelighedsevaluering).
    router.include_router(documents.router)
    router.include_router(applicability.router)
    router.include_router(vessel_applicability.router, prefix="/v1")
    if geography_enabled:
        router.include_router(geography.router)
    if international_catalogue_enabled:
        from . import catalogue
        router.include_router(catalogue.router)

    # Beskyttet (drift, import, vektorisering).
    if enable_admin_api:
        router.include_router(
            imports.router,
            dependencies=[AdminAuth],
            responses=ADMIN_RESPONSES,
        )
        if geography_enabled:
            router.include_router(
                geography.admin_router,
                dependencies=[AdminAuth],
                responses=ADMIN_RESPONSES,
            )

    return router


api_router = create_api_router(True)

__all__ = ["api_router", "create_api_router"]

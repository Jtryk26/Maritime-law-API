"""Samling af API'ets ruter.

Opdelingen i offentlige og beskyttede routere er en sikkerhedsgrænse, ikke kun
en filopdeling:

* `documents` er den offentlige læsegrænseflade — søgning, dokumenter,
  versioner, kategorier og facetter.
* `applicability` er den offentlige anvendelighedsvurdering. Den ser kun
  regler, et menneske har godkendt.
* `imports` er driftsgrænsefladen — importkørsler, vektorisering, nøgletal
  og importhistorik.
* `applicability_admin` er gennemgangskøen for regeludkast. Den hører hjemme
  bag samme token, fordi det er dér, et maskinelt udtræk bliver til en regel,
  systemet svarer ud fra.
* `vessel_applicability` er Vessel Applicability 2.0 under `/v1`: en
  offentlig vurdering og en router til udkast og godkendelse, der kræver
  et personligt anmeldertoken (REVIEWER_TOKENS) i stedet for det delte
  administratortoken.

De to sidste får administratorkravet på **routerniveau**, så en ny rute i de
filer er beskyttet med det samme. Det modsatte — at huske en dependency pr.
rute — er den fejl, der før eller siden bliver begået.
"""

from fastapi import APIRouter, Depends

from app.core.security import AdminAuth, require_reviewer

from . import applicability, applicability_admin, documents, imports, vessel_applicability

ADMIN_RESPONSES = {
    401: {"description": "Manglende eller ugyldigt administratortoken."},
    503: {"description": "Administratoradgang er ikke konfigureret på serveren."},
}

REVIEWER_RESPONSES = {
    401: {"description": "Manglende eller ukendt personligt anmeldertoken."},
    403: {"description": "Anmelderen mangler rollen, eller adskillelse af hverv er brudt."},
    503: {"description": "Ingen anmeldere er konfigureret på serveren."},
}


def create_api_router(enable_admin_api: bool = True) -> APIRouter:
    """Opretter API-routeren.

    Når `enable_admin_api` er False (f.eks. i ren offentlig produktion),
    udelades administrative og skrivende ruter fuldstændigt fra applikationen.
    """
    router = APIRouter()

    # Offentligt (Læsning og regelanvendelighedsevaluering).
    router.include_router(documents.router)
    router.include_router(applicability.router)
    router.include_router(vessel_applicability.router, prefix="/v1")

    # Beskyttet (Drift, import, vektorisering, regeludkast og godkendelseskø).
    if enable_admin_api:
        for _protected in (imports.router, applicability_admin.router):
            router.include_router(
                _protected,
                dependencies=[AdminAuth],
                responses=ADMIN_RESPONSES,
            )
        # Regeludkast og godkendelser: personligt anmeldertoken, ikke det delte
        # administratortoken. Ruterne kræver desuden hver sin rolle.
        router.include_router(
            vessel_applicability.admin_router,
            prefix="/v1",
            dependencies=[Depends(require_reviewer)],
            responses=REVIEWER_RESPONSES,
        )

    return router


api_router = create_api_router(True)

__all__ = ["api_router", "create_api_router"]

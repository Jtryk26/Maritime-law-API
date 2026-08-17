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

De to sidste får administratorkravet på **routerniveau**, så en ny rute i de
filer er beskyttet med det samme. Det modsatte — at huske en dependency pr.
rute — er den fejl, der før eller siden bliver begået.
"""

from fastapi import APIRouter

from app.core.security import AdminAuth

from . import applicability, applicability_admin, documents, imports

ADMIN_RESPONSES = {
    401: {"description": "Manglende eller ugyldigt administratortoken."},
    503: {"description": "Administratoradgang er ikke konfigureret på serveren."},
}

api_router = APIRouter()

# Offentligt.
api_router.include_router(documents.router)
api_router.include_router(applicability.router)

# Beskyttet.
for _protected in (imports.router, applicability_admin.router):
    api_router.include_router(
        _protected,
        dependencies=[AdminAuth],
        responses=ADMIN_RESPONSES,
    )

__all__ = ["api_router"]

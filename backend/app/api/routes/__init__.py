"""Samling af API'ets ruter.

Opdelingen i to routere er en sikkerhedsgrænse, ikke kun en filopdeling:

* `documents` er den offentlige læsegrænseflade — søgning, dokumenter,
  versioner, kategorier og facetter.
* `imports` er driftsgrænsefladen — importkørsler, vektorisering, nøgletal
  og importhistorik. Hele routeren kræver administratortoken, så en ny
  rute i den fil er beskyttet med det samme. Det modsatte — at huske en
  dependency pr. rute — er den fejl, der før eller siden bliver begået.
"""

from fastapi import APIRouter

from app.core.security import AdminAuth

from . import documents, imports

api_router = APIRouter()
api_router.include_router(documents.router)
api_router.include_router(
    imports.router,
    dependencies=[AdminAuth],
    responses={
        401: {"description": "Manglende eller ugyldigt administratortoken."},
        503: {"description": "Administratoradgang er ikke konfigureret på serveren."},
    },
)

__all__ = ["api_router"]

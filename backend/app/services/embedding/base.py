"""Kontrakten for embedding-udbydere.

Resten af systemet — chunk-indekseringen, vektorsøgningen og søgeloggen —
kender kun :class:`EmbeddingProvider`. Hvilken model der faktisk regner,
og om den kører i containeren eller bag et HTTP-endpoint, er isoleret i
`local.py`, `remote.py` og `hashing.py`.

Det er samme greb som `RelevanceEngine` og `RetsinformationClient`: en
udskiftning må aldrig kræve ændringer i importeren, databasen eller API'et.

Asymmetri
=========
E5-familien (og flere andre) er trænet med forskellige præfikser til
forespørgsler og til tekst der indekseres. Derfor to metoder frem for
én: :meth:`embed_passages` og :meth:`embed_query`. En model uden
præfikskrav implementerer dem blot ens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

__all__ = [
    "EmbeddingProvider",
    "ProviderInfo",
    "EmbeddingError",
    "EmbeddingUnavailableError",
    "EmbeddingDimensionError",
]


class EmbeddingError(RuntimeError):
    """Fejl i embedding-laget."""


class EmbeddingUnavailableError(EmbeddingError):
    """Modellen kunne ikke indlæses eller endpointet svarer ikke.

    Rejses bevidst frem for at falde tilbage til en anden udbyder. En
    tavs nedgradering ville betyde, at halvdelen af indekset var lavet
    med én model og resten med en anden — og at ingen opdagede det.
    """


class EmbeddingDimensionError(EmbeddingError):
    """Modellen leverede en anden vektorlængde end konfigurationen lover.

    Det er en konfigurationsfejl, ikke en driftsfejl: alle gemte vektorer
    ville blive ubrugelige sammen med de nye.
    """


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    """Selvbeskrivelse. Vises i `embed status`, /api/stats og i brugerfladen."""

    #: Kort navn på udbyderen: "local", "api", "hashing".
    provider: str
    #: Modelnavn. Gemmes på hvert chunk, så et modelskifte kan opdages.
    model: str
    dimensions: int
    #: Falsk for `hashing`. Brugerfladen skal ikke kalde noget
    #: "betydningssøgning", hvis vektorerne ikke bærer betydning.
    semantic: bool
    #: Kort forklaring til drift. Vises som den er.
    description: str = ""
    #: Foreslået nedre grænse for hvad der tælles som et semantisk hit.
    #:
    #: Hvad en "høj" cosinus-lighed er, afhænger fuldstændig af modellen.
    #: E5-familien lægger næsten alle par mellem 0,70 og 0,90, mens andre
    #: modeller spreder sig over hele intervallet. En grænse indstillet
    #: efter én model er derfor forkert for den næste, og udbyderen er det
    #: eneste sted der overhovedet ved noget om sin egen skala.
    #:
    #: Værdien er et STARTPUNKT, ikke en sandhed. Skal søgekvaliteten
    #: tages alvorligt, måles den på et sæt kendte søgninger og sættes i
    #: VECTOR_MIN_SIMILARITY. 0,0 betyder "jeg kender ikke skalaen" — da
    #: begrænses resultatet alene af top-k og sammensmeltningen.
    suggested_min_similarity: float = 0.0

    def to_json(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "dimensions": self.dimensions,
            "semantic": self.semantic,
            "description": self.description,
            "suggested_min_similarity": self.suggested_min_similarity,
        }


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Beregner vektorer for tekst."""

    info: ProviderInfo

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        """Vektorer for tekst der skal indekseres. Form: (len(texts), dim)."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """Vektor for en søgning. Form: (dim,)."""
        ...

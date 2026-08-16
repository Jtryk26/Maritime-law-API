"""Søgeabstraktion.

Fire implementeringer deler denne kontrakt:

* :class:`PostgresSearchBackend` — PostgreSQL fuldtekstsøgning (produktion).
* :class:`FallbackSearchBackend` — portabel token-søgning (SQLite, udvikling og test).
* :class:`VectorSearchBackend` — betydningssøgning på vektoriserede chunks.
* :class:`HybridSearchBackend` — sammensmeltning af de to slags.

API-laget kender kun kontrakten. Hvilken backend der svarer, afgøres af
databasen (leksikalsk) og af om der findes vektorer (semantisk).

Om de tre søgetilstande
=======================
``lexical``
    Ordene skal stå der. Uundværligt for juridisk arbejde: søger man
    "MARPOL bilag VI", vil man have de dokumenter der nævner netop det.

``semantic``
    Betydningen skal ligne. Finder "redningsflåde" når man skriver
    "livbåd", og "brandslukningsanlæg" når man skriver "sprinkler" —
    men kan komme til at overse en eksakt term, hvis den er sjælden.

``hybrid``
    Begge dele, smeltet sammen med Reciprocal Rank Fusion. Standarden,
    fordi de to fejler på hver sin måde: den leksikalske finder intet
    når ordvalget afviger, den semantiske er upræcis når ordvalget er
    præcist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.models import Document

__all__ = [
    "ScoredChunk",
    "SearchQuery",
    "SearchHit",
    "SearchResults",
    "SearchBackend",
    "SearchMode",
    "SEARCH_MODES",
]

#: Gyldige søgetilstande. Valideres i API-laget.
SEARCH_MODES = ("lexical", "semantic", "hybrid")
SearchMode = str


@dataclass(slots=True)
class SearchQuery:
    """Søgekriterier. Alle filtre er valgfrie og kombineres med AND."""

    q: str | None = None
    categories: list[str] = field(default_factory=list)
    document_types: list[str] = field(default_factory=list)
    authorities: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    #: Eksakt lov-/bekendtgørelsesnummer.
    document_number: str | None = None
    #: "kernelaw", "speciallaw", "support". Tom liste = alle.
    law_classes: list[str] = field(default_factory=list)
    published_from: date | None = None
    published_to: date | None = None
    min_score: int | None = None
    max_score: int | None = None
    is_maritime: bool | None = None
    sort: str = "relevance"  # relevance | date_desc | date_asc | score_desc | title
    #: lexical | semantic | hybrid. Se modulets docstring.
    mode: str = "lexical"
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


@dataclass(slots=True)
class SearchHit:
    """Ét søgeresultat med rangering og uddrag.

    De tre score-felter holdes adskilt med vilje. Når en bruger undrer
    sig over hvorfor et dokument står nummer ét, skal svaret kunne gives:
    stod ordene der, lignede betydningen, eller begge dele.
    """

    document: Document
    rank: float
    snippet: str
    #: Leksikalsk rang (ts_rank_cd eller tokenscore). None hvis kun semantisk hit.
    lexical_rank: float | None = None
    #: Cosinus-lighed 0–1 med dokumentets bedst matchende stykke.
    semantic_score: float | None = None
    #: "lexical", "semantic" eller "both". Vises i brugerfladen.
    match_source: str = "lexical"
    #: Overskriften på det stykke der matchede semantisk, f.eks. "§ 12".
    matched_heading: str | None = None
    #: Regnestykket bag placeringen: delscorer og anvendte domæneregler.
    #: :class:`app.services.ranking.RankingBreakdown`. None ved sortering
    #: på dato eller titel, hvor rangeringsmodellen ikke er i brug.
    ranking: object | None = None
    #: Den bedst matchende paragraf med kapitelkontekst.
    #: :class:`app.services.search.paragraphs.ParagraphHit`.
    paragraph: object | None = None
    #: Yderligere matchende paragraffer i samme dokument, bedste først.
    paragraphs: list = field(default_factory=list)


@dataclass(slots=True)
class SearchResults:
    """Resultatside med samlet antal."""

    hits: list[SearchHit]
    total: int
    page: int
    page_size: int
    backend: str
    #: Den tilstand der FAKTISK blev brugt. Kan afvige fra det ønskede:
    #: uden vektorer falder semantisk og hybrid tilbage til leksikalsk,
    #: og det skal kunne ses — ikke skjules.
    mode: str = "lexical"
    #: Fandtes der overhovedet vektorer at søge i?
    semantic_available: bool = False
    #: Sandt hvis kandidatloftet blev ramt, så `total` er et undertal.
    #: Hybridsøgning tæller kandidater, ikke hele databasen — se
    #: `hybrid.py` for hvorfor.
    truncated: bool = False
    #: Kort forklaring, hvis den ønskede tilstand ikke kunne leveres.
    notice: str | None = None
    #: Hvordan søgestrengen blev forstået: bred, semispecifik eller niche.
    #: :class:`app.services.ranking.QueryIntent`. Afgør domænereglerne og
    #: vises i brugerfladen, så en uventet rækkefølge kan forklares.
    intent: object | None = None

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


@dataclass(slots=True)
class ScoredChunk:
    """Et vektormatch: ét stykke lovtekst og dets lighed med søgningen."""

    chunk_id: int
    document_id: int
    similarity: float
    content: str
    heading: str | None = None


@runtime_checkable
class SearchBackend(Protocol):
    """Kontrakt for søgning."""

    name: str

    def search(self, session: Session, query: SearchQuery) -> SearchResults:
        ...

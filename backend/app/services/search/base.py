"""Søgeabstraktion.

To implementeringer deler denne kontrakt:

* :class:`PostgresSearchBackend` — PostgreSQL fuldtekstsøgning (produktion).
* :class:`FallbackSearchBackend` — portabel token-søgning (SQLite, udvikling og test).

API-laget kender kun kontrakten, så backend vælges ud fra databasen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.models import Document

__all__ = ["SearchQuery", "SearchHit", "SearchResults", "SearchBackend"]


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
    published_from: date | None = None
    published_to: date | None = None
    min_score: int | None = None
    max_score: int | None = None
    is_maritime: bool | None = None
    sort: str = "relevance"  # relevance | date_desc | date_asc | score_desc | title
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


@dataclass(slots=True)
class SearchHit:
    """Ét søgeresultat med rangering og uddrag."""

    document: Document
    rank: float
    snippet: str


@dataclass(slots=True)
class SearchResults:
    """Resultatside med samlet antal."""

    hits: list[SearchHit]
    total: int
    page: int
    page_size: int
    backend: str

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


@runtime_checkable
class SearchBackend(Protocol):
    """Kontrakt for søgning."""

    name: str

    def search(self, session: Session, query: SearchQuery) -> SearchResults:
        ...

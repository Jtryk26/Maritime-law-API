"""Kontrakt for kandidat-opdagelse (discovery).

Discovery besvarer ét spørgsmål: *hvilke accessionsnumre findes der
overhovedet?* Den henter ikke dokumenter, klassificerer ikke og skriver
ikke i databasen. Resultatet er en liste kandidater, der gennemgås af et
menneske i en CSV, før noget lægges i produktionskøen.

Adskillelsen fra :mod:`app.services.backfill` er bevidst:

* ``discovery``  — finder numre (kan fejle, kan give for mange, skal ses efter).
* ``backfill``   — kører kendte numre igennem importen (genoptagelig, samtidig).

Rækkefølgen er altid: discover → gennemse CSV → enqueue-manifest → run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DiscoveryError",
    "DiscoveryHit",
    "DiscoveryQuery",
    "DiscoveryResult",
    "DiscoveryClient",
    "DiscoveryConfigurationError",
    "DiscoveryPaginationError",
    "DiscoveryResponseError",
]


class DiscoveryError(RuntimeError):
    """Basisfejl for opdagelse af kandidater."""


class DiscoveryConfigurationError(DiscoveryError):
    """Søgegrænsefladen er ikke konfigureret — eller er konfigureret forkert.

    Rejses bevidst frem for at gætte en URL. Se ``docs`` i
    :mod:`app.services.discovery.search_client`.
    """


class DiscoveryResponseError(DiscoveryError):
    """Kilden svarede, men svaret kunne ikke tolkes som en resultatliste."""


class DiscoveryPaginationError(DiscoveryError):
    """Paginering virker ikke som antaget.

    Rejses hvis side *n+1* leverer nøjagtig de samme accessionsnumre som
    side *n*. Uden denne kontrol ville en ignoreret sideparameter give en
    uendelig løkke, der henter side 1 igen og igen.
    """


@dataclass(slots=True, frozen=True)
class DiscoveryQuery:
    """Én afgrænset søgning mod kilden.

    `status` er kildens eget statusfilter (f.eks. "Gældende" eller
    "Historisk"), ikke systemets normaliserede status. Discovery arbejder
    med kildens vokabular; normaliseringen sker først ved import.
    """

    authority: str
    status: str | None = None
    #: Menneskeligt navn, skrives til CSV-kolonnen ``source_query``.
    label: str = "alle"
    #: Ekstra parametre der flettes ind i anmodningsskabelonen.
    extra: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        parts = [f"administrerendeMyndighed={self.authority}"]
        if self.status:
            parts.append(f"status={self.status}")
        return "&".join(parts)


@dataclass(slots=True)
class DiscoveryHit:
    """Én kandidat fundet hos kilden.

    Alle felter ud over `accession_number` er valgfrie: forskellige
    svarformater bærer forskellige metadata, og manglende metadata må
    aldrig få opdagelsen til at fejle. Nummeret er det eneste, køen
    behøver.
    """

    accession_number: str
    title: str | None = None
    authority: str | None = None
    status: str | None = None
    document_type: str | None = None
    published_date: date | None = None
    eli_url: str | None = None
    #: Hvilken søgning nummeret kom fra. Bevares hele vejen til CSV'en.
    source_query: str = ""
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: Kildens egen post, uændret. Kun til fejlsøgning; skrives ikke til CSV.
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveryResult:
    """Svaret på én :class:`DiscoveryQuery`."""

    query: DiscoveryQuery
    hits: list[DiscoveryHit] = field(default_factory=list)
    #: Antallet kilden selv oplyser, hvis svaret indeholder det.
    reported_total: int | None = None
    pages_fetched: int = 0
    truncated: bool = False

    @property
    def count(self) -> int:
        return len(self.hits)


@runtime_checkable
class DiscoveryClient(Protocol):
    """Kontrakt for alt der kan finde kandidat-accessionsnumre.

    Implementeringer:
      * :class:`~app.services.discovery.fixture.FixtureDiscoveryClient`
      * :class:`~app.services.discovery.search_client.RetsinformationSearchClient`

    En senere ELI-/SPARQL- eller sitemap-baseret opdagelse kan tilføjes
    uden at røre :class:`~app.services.discovery.service.DiscoveryService`.
    """

    kind: str

    def search(self, query: DiscoveryQuery) -> DiscoveryResult:
        """Kører én søgning færdig, inklusive paginering."""
        ...

    def close(self) -> None:
        """Frigiver eventuelle ressourcer."""
        ...

"""Kildeabstraktion for Retsinformation.

Resten af applikationen kender kun :class:`NormalizedDocument` og
:class:`SourceClient`. Ingen anden del af systemet må afhænge af
Retsinformations konkrete JSON- eller XML-strukturer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Protocol, runtime_checkable

__all__ = [
    "DocumentRef",
    "NormalizedDocument",
    "SourceClient",
    "SourceError",
    "DocumentNotFoundError",
    "TransientSourceError",
    "PermanentSourceError",
]


# ---------------------------------------------------------------------------
# Fejltyper
# ---------------------------------------------------------------------------


class SourceError(RuntimeError):
    """Basisfejl for kildeintegration."""


class DocumentNotFoundError(SourceError):
    """Dokumentet findes ikke hos kilden."""


class TransientSourceError(SourceError):
    """Midlertidig fejl — kan forsøges igen (timeout, 5xx, 429)."""


class PermanentSourceError(SourceError):
    """Permanent fejl — gentagne forsøg er nytteløse (4xx bortset fra 429)."""


# ---------------------------------------------------------------------------
# Datastrukturer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DocumentRef:
    """Let reference til et dokument hos kilden.

    Returneres af listeoperationer. Indeholder nok til at afgøre om
    dokumentet skal hentes fuldt ud.
    """

    source_id: str
    title: str | None = None
    document_type: str | None = None
    source_url: str | None = None
    retsinformation_id: str | None = None
    document_number: str | None = None
    change_date: date | None = None
    reason_for_change: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedDocument:
    """Systemets interne dokumentrepræsentation.

    Alle felter er normaliseret. `raw_metadata` bevarer kildens egne
    data uændret af hensyn til sporbarhed og senere genfortolkning.
    """

    source: str
    source_id: str
    title: str
    content: str = ""

    short_title: str | None = None
    document_type: str | None = None
    authority: str | None = None
    published_date: date | None = None
    effective_date: date | None = None
    status: str | None = None
    source_url: str | None = None
    retsinformation_id: str | None = None
    #: Lov-/bekendtgørelsesnummer som praktikere kender det, f.eks. "1290".
    document_number: str | None = None
    #: True for fixturdata. Må ALDRIG præsenteres som officiel kilde.
    is_synthetic: bool = False
    #: Hvad kilden faktisk leverede: "full_text", "metadata_only",
    #: "text_without_paragraph_sign" eller "empty". Se
    #: :mod:`app.services.legal.content_kind`. Uden feltet kan man ikke
    #: se forskel på et dokument vi mangler tekst til, og et dokument
    #: kilden ikke HAR tekst til.
    content_kind: str | None = None

    # Ekstra normaliserede metadata (nøgleord, ressort, ministerium ...).
    metadata: dict[str, Any] = field(default_factory=dict)
    # Uændret kildeleverance.
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    retrieved_at: datetime | None = None

    def metadata_text(self) -> str:
        """Samlet metadatatekst brugt af relevans- og kategoriseringsmotor."""
        parts: list[str] = []
        if self.short_title:
            parts.append(self.short_title)
        if self.document_type:
            parts.append(self.document_type)
        if self.status:
            parts.append(self.status)
        for key, value in self.metadata.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                parts.extend(str(item) for item in value)
            elif isinstance(value, (str, int, float)):
                parts.append(str(value))
            else:
                continue
            # Nøglen selv er sjældent informativ, men f.eks. "ressort" kan være.
            parts.append(str(key))
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Klientkontrakt
# ---------------------------------------------------------------------------


@runtime_checkable
class SourceClient(Protocol):
    """Kontrakt som alle Retsinformation-klienter opfylder.

    Implementeringer:
      * :class:`FixtureRetsinformationClient` — lokale testdata, offline.
      * :class:`ProductionRetsinformationClient` — officiel høsteservice.
    """

    #: Kort maskinnavn, gemmes på importkørslen ("fixture" / "production").
    kind: str

    def get_documents(
        self,
        *,
        since: date | None = None,
        explicit_ids: Iterable[str] | None = None,
    ) -> Iterable[DocumentRef]:
        """Returnerer referencer til dokumenter, der skal behandles.

        `explicit_ids` henter netop de angivne kilde-id'er uden om
        ændringsfeeden. Det er den eneste vej til historisk
        efterindlæsning, da feeden kun rækker ti dage tilbage.
        """
        ...

    def get_updated_documents(self, since: date) -> Iterable[DocumentRef]:
        """Returnerer dokumenter ændret siden den angivne dato."""
        ...

    def get_document(self, document_id: str) -> NormalizedDocument:
        """Henter og normaliserer et komplet dokument inkl. tekst."""
        ...

    def get_document_metadata(self, document_id: str) -> NormalizedDocument:
        """Henter dokumentet uden nødvendigvis at hente hele brødteksten."""
        ...

    def get_document_text(self, document_id: str) -> str:
        """Henter den fulde lovtekst som ren tekst."""
        ...

    def close(self) -> None:
        """Frigiver eventuelle ressourcer."""
        ...

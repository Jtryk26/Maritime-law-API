"""Kontrakt for maritim kategorisering.

Adskilt fra relevansmotoren, så de to kan udvikles og udskiftes
uafhængigt. Importeren kender kun denne grænseflade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.services.retsinformation.base import NormalizedDocument

__all__ = ["CategoryAssignment", "CategorizationResult", "CategoryDefinition", "CategorizationEngine"]


@dataclass(slots=True, frozen=True)
class CategoryDefinition:
    """En kategori som defineret i config/categories.yaml."""

    slug: str
    name: str
    description: str | None = None
    sort_order: int = 0


@dataclass(slots=True)
class CategoryAssignment:
    """En kategori tildelt et dokument."""

    slug: str
    name: str
    confidence: float
    matched_terms: list[str] = field(default_factory=list)
    raw_score: float = 0.0
    is_fallback: bool = False


@dataclass(slots=True)
class CategorizationResult:
    """Samlet resultat af kategoriseringen."""

    assignments: list[CategoryAssignment] = field(default_factory=list)
    engine: str = "unknown"

    @property
    def slugs(self) -> list[str]:
        return [a.slug for a in self.assignments]


@runtime_checkable
class CategorizationEngine(Protocol):
    """Enhver kategoriseringsmotor opfylder denne kontrakt."""

    name: str

    def categorize(self, document: NormalizedDocument) -> CategorizationResult:
        """Tildeler maritime kategorier til dokumentet."""
        ...

    def definitions(self) -> list[CategoryDefinition]:
        """Alle kendte kategorier — bruges til seeding af databasen."""
        ...

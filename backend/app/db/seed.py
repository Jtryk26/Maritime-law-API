"""Seeding af kategorier fra konfigurationen.

Kategorierne er en del af domænemodellen, ikke brugerdata. De skal
findes i databasen før første import, så tildelinger kan gemmes.
Operationen er idempotent og kan køres ved hver opstart.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services.categorization import get_categorization_engine
from app.services.importer import DocumentRepository

logger = get_logger(__name__)

__all__ = ["seed_categories"]


def seed_categories(session: Session) -> int:
    """Opretter eller opdaterer alle kategorier. Returnerer antallet."""
    definitions = get_categorization_engine().definitions()
    DocumentRepository(session).sync_categories(definitions)
    session.commit()
    logger.info("db.seed.categories", extra={"count": len(definitions)})
    return len(definitions)

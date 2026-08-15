"""Opdager om databasen kan indeksere vektorer.

Systemet skal køre både på PostgreSQL med pgvector (produktion), på
PostgreSQL uden (en almindelig postgres-image), og på SQLite (udvikling
og test). Søgelaget skal kunne vælge sti uden at gætte.

Svaret caches pr. motor, fordi det ikke kan ændre sig i en kørende
proces, og fordi et opslag i systemkatalogerne ved hver eneste søgning
ville være unødigt.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["has_pgvector", "vector_column_dimensions", "reset_vector_support_cache"]

_CACHE: dict[int, bool] = {}
_DIM_CACHE: dict[int, int | None] = {}


def _engine_key(session: Session) -> int:
    return id(session.get_bind())


def has_pgvector(session: Session) -> bool:
    """Sandt hvis pgvector er installeret OG kolonnerne findes.

    Begge dele skal være opfyldt: udvidelsen kan være installeret efter
    at migration 0004 kørte, og da findes kolonnen ikke.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return False

    key = _engine_key(session)
    if key in _CACHE:
        return _CACHE[key]

    try:
        found = session.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'document_chunks'
                  AND column_name = 'embedding_vec'
                """
            )
        ).first()
        available = found is not None
    except Exception:  # noqa: BLE001 - må aldrig vælte en søgning
        logger.warning("vector.support_check_failed", exc_info=True)
        available = False

    if not available:
        logger.info(
            "vector.pgvector_absent",
            extra={
                "note": (
                    "Kolonnen document_chunks.embedding_vec findes ikke. "
                    "Vektorsøgning bruger den portable brute force-sti."
                )
            },
        )
    _CACHE[key] = available
    return available


def vector_column_dimensions(session: Session) -> int | None:
    """Dimensionen pgvector-kolonnen blev oprettet med, hvis den findes.

    Bruges af `embed status` til at fange den ubehagelige situation, hvor
    embedding-modellen er skiftet til en anden vektorlængde end den
    kolonnen har — da vil enhver vektorsøgning fejle i databasen.
    """
    if not has_pgvector(session):
        return None

    key = _engine_key(session)
    if key in _DIM_CACHE:
        return _DIM_CACHE[key]

    try:
        # atttypmod bærer dimensionen for pgvector's vector-type.
        row = session.execute(
            text(
                """
                SELECT a.atttypmod
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                WHERE c.relname = 'document_chunks'
                  AND a.attname = 'embedding_vec'
                """
            )
        ).first()
        dimensions = int(row[0]) if row and row[0] and row[0] > 0 else None
    except Exception:  # noqa: BLE001
        logger.warning("vector.dimension_check_failed", exc_info=True)
        dimensions = None

    _DIM_CACHE[key] = dimensions
    return dimensions


def reset_vector_support_cache() -> None:
    """Rydder cachen. Bruges af test og efter skemaændringer."""
    _CACHE.clear()
    _DIM_CACHE.clear()

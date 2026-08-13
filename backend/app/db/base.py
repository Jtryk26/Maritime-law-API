"""SQLAlchemy deklarativ base og fælles kolonnetyper."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Eksplicit navngivningskonvention, så Alembic kan autogenerere
# stabile constraint-navne på tværs af databaser.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """Tidsstempel med tidszone. Bruges som default i modellerne."""
    return datetime.now(timezone.utc)


def timestamp_column(**kwargs):
    """DateTime-kolonne med tidszone."""
    return mapped_column(DateTime(timezone=True), **kwargs)

"""Kørsel af Alembic-migrationer fra applikationskoden.

Bruges ved opstart i Docker, så en ny udvikler ikke skal køre
migrationer manuelt. Migrationsfilerne er stadig den eneste kilde til
skemaet — der oprettes aldrig tabeller ad hoc med create_all.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["run_migrations", "alembic_config"]

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def run_migrations() -> None:
    """Bringer databasen op på nyeste revision."""
    logger.info("db.migrations.upgrade.start")
    command.upgrade(alembic_config(), "head")
    logger.info("db.migrations.upgrade.done")

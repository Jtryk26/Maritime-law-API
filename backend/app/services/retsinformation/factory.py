"""Valg af kildeklient.

VIGTIG REGEL
============
Produktionsklienten falder ALDRIG tilbage til fixturdata. Beder man om
produktionskilden, og den ikke kan bruges, fejler kaldet i stedet for at
levere syntetiske dokumenter. I et system der bruges til at slå
lovgivning op, ville en stille erstatning med opdigtet indhold være en
alvorlig fejl — værre end en fejlmeddelelse.

Fixturdata er derfor altid et bevidst valg (`SOURCE_CLIENT=fixture`),
og hvert fixturdokument bærer `is_synthetic=True` hele vejen gennem
databasen til brugerfladen.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger

from .base import SourceClient
from .fixture import FixtureRetsinformationClient
from .production import ProductionRetsinformationClient

logger = get_logger(__name__)

__all__ = ["build_source_client", "UnknownSourceClientError"]

FIXTURE = "fixture"
PRODUCTION = "production"
VALID_KINDS = (FIXTURE, PRODUCTION)


class UnknownSourceClientError(ValueError):
    """Konfigurationen peger på en kilde der ikke findes."""


def build_source_client(
    kind: str | None = None,
    *,
    fixture_revision: int = 1,
) -> SourceClient:
    """Bygger den kildeklient konfigurationen beder om.

    Args:
        kind: "fixture" eller "production". Standard fra SOURCE_CLIENT.
        fixture_revision: Hvilket fixtursæt der indlæses (kun for fixture).

    Raises:
        UnknownSourceClientError: Ved ukendt værdi. Der vælges bevidst
            ikke en standard, da et tastefejlet miljøvariabelnavn ellers
            kunne føre til at syntetiske data blev importeret i drift.
    """
    settings = get_settings()
    # Kun None betyder "brug konfigurationens standard". En eksplicit tom
    # streng er en fejl — ellers ville en tom miljøvariabel kunne føre til
    # at fixturdata blev valgt uden at nogen havde bedt om det.
    raw = settings.source_client if kind is None else kind
    resolved = (raw or "").strip().lower()

    if resolved == PRODUCTION:
        logger.info("source.client.selected", extra={"kind": PRODUCTION})
        return ProductionRetsinformationClient()

    if resolved == FIXTURE:
        logger.warning(
            "source.client.selected",
            extra={
                "kind": FIXTURE,
                "revision": fixture_revision,
                "note": "SYNTETISKE-TESTDATA-ikke-officiel-kilde",
            },
        )
        return FixtureRetsinformationClient(revision=fixture_revision)

    raise UnknownSourceClientError(
        f"Ukendt kildeklient {resolved!r}. Gyldige værdier: {', '.join(VALID_KINDS)}. "
        "Der vælges ikke en standard, da fixturdata aldrig må importeres utilsigtet."
    )

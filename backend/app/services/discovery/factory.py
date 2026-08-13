"""Valg af opdagelsesklient.

Samme regel som for kildeklienten i
:mod:`app.services.retsinformation.factory`: der falder **aldrig**
automatisk tilbage til fixturdata. Et manifest bygget på syntetiske
søgeresultater ville lægge opdigtede accessionsnumre i produktionskøen.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger

from .base import DiscoveryClient
from .fixture import FixtureDiscoveryClient
from .search_client import RetsinformationSearchClient

logger = get_logger(__name__)

__all__ = ["build_discovery_client", "UnknownDiscoveryClientError"]

FIXTURE = "fixture"
PRODUCTION = "production"
VALID_KINDS = (FIXTURE, PRODUCTION)


class UnknownDiscoveryClientError(ValueError):
    """Konfigurationen peger på en opdagelsesklient der ikke findes."""


def build_discovery_client(kind: str | None = None) -> DiscoveryClient:
    """Bygger den opdagelsesklient der bedes om.

    Args:
        kind: "fixture" eller "production". Standard fra SOURCE_CLIENT.
    """
    settings = get_settings()
    raw = settings.source_client if kind is None else kind
    resolved = (raw or "").strip().lower()

    if resolved == PRODUCTION:
        logger.info("discovery.client.selected", extra={"kind": PRODUCTION})
        return RetsinformationSearchClient()

    if resolved == FIXTURE:
        logger.warning(
            "discovery.client.selected",
            extra={"kind": FIXTURE, "note": "SYNTETISKE-TESTDATA-ikke-officiel-kilde"},
        )
        return FixtureDiscoveryClient()

    raise UnknownDiscoveryClientError(
        f"Ukendt opdagelsesklient {resolved!r}. Gyldige værdier: {', '.join(VALID_KINDS)}."
    )

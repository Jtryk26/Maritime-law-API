"""Opdagelse af kandidat-accessionsnumre.

Retsinformations høsteservice kan ikke liste lovsamlingen — den er en
ændringsfeed med ti dages tilbagekig. Historisk efterindlæsning kræver
derfor en liste af accessionsnumre udefra. Denne pakke skaffer den
liste og lægger den i en CSV til menneskelig gennemgang.

Kæden er::

    backfill discover          → manifests/*.csv   (intet i kø)
    (gennemgang af CSV'en)
    backfill enqueue-manifest  → køen
    backfill run               → importpipelinen

Pakken skriver aldrig i databasen.
"""

from __future__ import annotations

from app.services.discovery.base import (
    DiscoveryClient,
    DiscoveryConfigurationError,
    DiscoveryError,
    DiscoveryHit,
    DiscoveryPaginationError,
    DiscoveryQuery,
    DiscoveryResponseError,
    DiscoveryResult,
)
from app.services.discovery.factory import build_discovery_client
from app.services.discovery.manifest_csv import (
    COLUMNS,
    DEFAULT_DECISION,
    ManifestRow,
    read_manifest,
    write_manifest,
)
from app.services.discovery.service import (
    SOEFARTSSTYRELSEN_GROUPS,
    VERIFIED_COUNTS,
    DiscoveryGroup,
    DiscoveryReport,
    DiscoveryService,
    DiscoveryValidationError,
)

__all__ = [
    "COLUMNS",
    "DEFAULT_DECISION",
    "DiscoveryClient",
    "DiscoveryConfigurationError",
    "DiscoveryError",
    "DiscoveryGroup",
    "DiscoveryHit",
    "DiscoveryPaginationError",
    "DiscoveryQuery",
    "DiscoveryReport",
    "DiscoveryResponseError",
    "DiscoveryResult",
    "DiscoveryService",
    "DiscoveryValidationError",
    "ManifestRow",
    "SOEFARTSSTYRELSEN_GROUPS",
    "VERIFIED_COUNTS",
    "build_discovery_client",
    "read_manifest",
    "write_manifest",
]

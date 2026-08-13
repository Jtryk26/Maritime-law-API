"""Fixture-klient til opdagelse.

Gør hele kæden ``discover → CSV → enqueue-manifest → run`` kørbar og
testbar uden netværksadgang. Data er SYNTETISKE og må aldrig præsenteres
som resultater fra Retsinformation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

from .base import DiscoveryHit, DiscoveryQuery, DiscoveryResult
from .extract import extract_hit_fields

logger = get_logger(__name__)

__all__ = ["FixtureDiscoveryClient", "FIXTURE_FILE", "SYNTHETIC_NOTICE"]

FIXTURE_FILE = "discovery_soefartsstyrelsen.json"

SYNTHETIC_NOTICE = (
    "SYNTETISKE SØGERESULTATER. Konstrueret til udvikling og test af "
    "opdagelsespipelinen. Ikke hentet fra Retsinformation."
)


class FixtureDiscoveryClient:
    """Læser kandidater fra en lokal JSON-fil.

    Opfylder :class:`~app.services.discovery.base.DiscoveryClient`.
    Filens poster filtreres på myndighed og status præcis som den rigtige
    søgning ville gøre, så tællekontrollen i
    :class:`~app.services.discovery.service.DiscoveryService` kan afprøves.
    """

    kind = "fixture"

    def __init__(
        self,
        *,
        fixture_dir: Path | None = None,
        records: list[dict[str, Any]] | None = None,
    ) -> None:
        if records is not None:
            self._records = list(records)
            return

        path = Path(fixture_dir or get_settings().fixture_dir) / FIXTURE_FILE
        payload = json.loads(path.read_text(encoding="utf-8"))
        self._records = list(payload.get("results", []))
        logger.info(
            "discovery.fixture.loaded",
            extra={"path": str(path), "records": len(self._records)},
        )

    def search(self, query: DiscoveryQuery) -> DiscoveryResult:
        result = DiscoveryResult(query=query, pages_fetched=1)

        for record in self._records:
            fields = extract_hit_fields(record)
            if not fields["accession_number"]:
                continue
            if not _matches(fields.get("authority"), query.authority):
                continue
            if query.status and not _matches(fields.get("status"), query.status):
                continue
            result.hits.append(
                DiscoveryHit(**fields, source_query=query.describe(), raw=record)
            )

        result.reported_total = len(result.hits)
        return result

    def close(self) -> None:  # pragma: no cover - ingen ressourcer
        return None


def _matches(value: str | None, wanted: str) -> bool:
    if not value:
        return False
    return value.strip().casefold() == wanted.strip().casefold()

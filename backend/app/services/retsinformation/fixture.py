"""Fixture-klient med lokale testdata.

Gør hele systemet kørbart og testbart uden netværksadgang til
Retsinformation. Data ligger i `data/fixtures/` og er SYNTETISKE —
se `SYNTHETIC_NOTICE` og fixturfilernes eget `_notice`-felt.

Fixtursættet har revisioner, så versionering kan demonstreres:

    revision 1  ->  documents.json          (grundsæt)
    revision 2  ->  documents_rev2.json     (ændringer oveni revision 1)
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.text import normalize_whitespace

from .base import DocumentNotFoundError, DocumentRef, NormalizedDocument
from .normalization import map_document_type, map_status, parse_danish_date

logger = get_logger(__name__)

SYNTHETIC_NOTICE = (
    "SYNTETISK TESTDATA. Disse dokumenter er konstrueret til udvikling og test "
    "af denne applikation. De er ikke hentet fra Retsinformation og er ikke "
    "gældende dansk ret."
)

BASE_FIXTURE = "documents.json"
REVISION_FIXTURES = {
    1: [BASE_FIXTURE],
    2: [BASE_FIXTURE, "documents_rev2.json"],
}


class FixtureRetsinformationClient:
    """Læser dokumenter fra lokale JSON-fixturer.

    Opfylder :class:`~app.services.retsinformation.base.SourceClient`.
    """

    kind = "fixture"

    def __init__(
        self,
        *,
        fixture_dir: Path | None = None,
        revision: int = 1,
        documents: Iterable[dict[str, Any]] | None = None,
    ) -> None:
        self.fixture_dir = Path(fixture_dir or get_settings().fixture_dir)
        self.revision = revision
        self._documents: dict[str, dict[str, Any]] = {}

        if documents is not None:
            # Direkte injektion — bruges af tests.
            for record in documents:
                self._documents[str(record["source_id"])] = record
        else:
            self._load_revision(revision)

        logger.info(
            "fixture.loaded",
            extra={"revision": revision, "documents": len(self._documents)},
        )

    # -- Indlæsning ---------------------------------------------------------

    def _load_revision(self, revision: int) -> None:
        filenames = REVISION_FIXTURES.get(revision)
        if filenames is None:
            raise ValueError(
                f"Ukendt fixtur-revision {revision}. Gyldige: {sorted(REVISION_FIXTURES)}"
            )

        for filename in filenames:
            path = self.fixture_dir / filename
            if not path.exists():
                raise FileNotFoundError(f"Fixturfil mangler: {path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            for record in payload.get("documents", []):
                # Senere revisioner erstatter tidligere poster med samme id.
                self._documents[str(record["source_id"])] = record

    # -- Kildekontrakt ------------------------------------------------------

    def get_documents(self, *, since: date | None = None) -> list[DocumentRef]:
        refs = [self._to_ref(record) for record in self._documents.values()]
        if since is not None:
            refs = [r for r in refs if r.change_date is None or r.change_date >= since]
        return refs

    def get_updated_documents(self, since: date) -> list[DocumentRef]:
        return self.get_documents(since=since)

    def get_document(self, document_id: str) -> NormalizedDocument:
        return self._normalize(self._record(document_id))

    def get_document_metadata(self, document_id: str) -> NormalizedDocument:
        doc = self._normalize(self._record(document_id))
        doc.content = ""
        return doc

    def get_document_text(self, document_id: str) -> str:
        return str(self._record(document_id).get("content", ""))

    def close(self) -> None:
        """Ingen ressourcer at frigive."""

    # -- Intern -------------------------------------------------------------

    def _record(self, document_id: str) -> dict[str, Any]:
        try:
            return self._documents[str(document_id)]
        except KeyError as exc:
            raise DocumentNotFoundError(
                f"Fixturdokument findes ikke: {document_id}"
            ) from exc

    def _to_ref(self, record: dict[str, Any]) -> DocumentRef:
        return DocumentRef(
            source_id=str(record["source_id"]),
            title=record.get("title"),
            document_type=map_document_type(record.get("document_type")),
            source_url=record.get("source_url"),
            retsinformation_id=record.get("retsinformation_id"),
            document_number=record.get("document_number"),
            change_date=parse_danish_date(record.get("change_date")),
            reason_for_change=record.get("reason_for_change", "FixtureData"),
            raw=record,
        )

    def _normalize(self, record: dict[str, Any]) -> NormalizedDocument:
        return NormalizedDocument(
            source="retsinformation-fixture",
            source_id=str(record["source_id"]),
            title=normalize_whitespace(record.get("title", "")),
            content=record.get("content", ""),
            short_title=record.get("short_title"),
            document_type=map_document_type(record.get("document_type")),
            authority=record.get("authority"),
            published_date=parse_danish_date(record.get("published_date")),
            effective_date=parse_danish_date(record.get("effective_date")),
            status=map_status(record.get("status")),
            source_url=record.get("source_url"),
            retsinformation_id=record.get("retsinformation_id"),
            document_number=record.get("document_number"),
            is_synthetic=True,
            metadata={
                "keywords": record.get("keywords", []),
                "ministry": record.get("ministry"),
                "document_number": record.get("document_number"),
                "synthetic": True,
            },
            raw_metadata={"_notice": SYNTHETIC_NOTICE, **record},
            retrieved_at=datetime.now(timezone.utc),
        )

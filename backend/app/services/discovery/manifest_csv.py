"""Revisionsvenlig CSV mellem opdagelse og kø.

Filen er bevidst det eneste bindeled mellem ``discover`` og ``enqueue``.
Opdagelsen skriver den; et menneske gennemgår den; først derefter går
noget i produktionskøen. Derfor:

* **Faste kolonner i fast rækkefølge** — så en git-diff mellem to
  opdagelser er læsbar.
* **Sortering på accessionsnummer** — så rækkefølgen ikke afhænger af
  kildens paginering, og to kørsler kan sammenlignes linje for linje.
* **``decision``-kolonnen** — ``include`` lægges i kø, alt andet springes
  over. Standard er ``include``; den der gennemgår filen skriver
  ``exclude`` (eller hvad som helst andet) ud for det, der ikke skal med.
* **UTF-8 med BOM** — filen åbnes typisk i Excel, som ellers viser
  ``Søfartsstyrelsen`` forkert.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from app.core.logging import get_logger

from .base import DiscoveryHit

logger = get_logger(__name__)

__all__ = [
    "COLUMNS",
    "DEFAULT_DECISION",
    "ManifestRow",
    "read_manifest",
    "write_manifest",
]

COLUMNS: Sequence[str] = (
    "accession_number",
    "title",
    "authority",
    "status",
    "document_type",
    "published_date",
    "eli_url",
    "source_query",
    "discovered_at",
    "decision",
)

DEFAULT_DECISION = "include"

_ENCODING = "utf-8-sig"


@dataclass(slots=True, frozen=True)
class ManifestRow:
    """Én indlæst CSV-linje."""

    accession_number: str
    decision: str
    title: str = ""
    status: str = ""
    source_query: str = ""


def write_manifest(
    path: Path | str,
    hits: Iterable[DiscoveryHit],
    *,
    decision: str = DEFAULT_DECISION,
    header_comment: str | None = None,
) -> int:
    """Skriver manifestet. Returnerer antal linjer.

    `header_comment` skrives som en ``#``-linje øverst — bruges til at
    mærke syntetiske kørsler, så en fixtur-CSV ikke kan forveksles med
    rigtige søgeresultater.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    rows = sorted(hits, key=lambda hit: hit.accession_number)

    with target.open("w", encoding=_ENCODING, newline="") as handle:
        if header_comment:
            handle.write(f"# {header_comment}\n")
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for hit in rows:
            writer.writerow(
                {
                    "accession_number": hit.accession_number,
                    "title": hit.title or "",
                    "authority": hit.authority or "",
                    "status": hit.status or "",
                    "document_type": hit.document_type or "",
                    "published_date": (
                        hit.published_date.isoformat() if hit.published_date else ""
                    ),
                    "eli_url": hit.eli_url or "",
                    "source_query": hit.source_query,
                    "discovered_at": hit.discovered_at.isoformat(timespec="seconds"),
                    "decision": decision,
                }
            )

    logger.info("discovery.manifest.written", extra={"path": str(target), "rows": len(rows)})
    return len(rows)


def read_manifest(path: Path | str) -> list[ManifestRow]:
    """Læser manifestet.

    Kommentarlinjer (``#``) og linjer uden accessionsnummer springes over.
    Dubletter fjernes — første forekomst vinder — så en manuelt redigeret
    fil ikke kan lægge samme nummer i kø to gange.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Manifestet findes ikke: {source}")

    rows: list[ManifestRow] = []
    seen: set[str] = set()

    with source.open("r", encoding=_ENCODING, newline="") as handle:
        lines = [line for line in handle if not line.lstrip().startswith("#")]

    reader = csv.DictReader(lines)
    missing = [column for column in ("accession_number", "decision") if column not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(
            f"Manifestet mangler kolonnen/kolonnerne {', '.join(missing)}. "
            f"Forventede kolonner: {', '.join(COLUMNS)}"
        )

    for record in reader:
        accession_number = (record.get("accession_number") or "").strip()
        if not accession_number or accession_number in seen:
            continue
        seen.add(accession_number)
        rows.append(
            ManifestRow(
                accession_number=accession_number,
                decision=(record.get("decision") or "").strip().casefold(),
                title=(record.get("title") or "").strip(),
                status=(record.get("status") or "").strip(),
                source_query=(record.get("source_query") or "").strip(),
            )
        )

    return rows

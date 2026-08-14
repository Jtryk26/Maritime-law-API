"""CSV-manifest for global opdagelse.

Samme princip som :mod:`app.services.discovery.manifest_csv`: filen er det
ENESTE bindeled mellem opdagelse og kø, gennemgås af et menneske, og
`enqueue-manifest` rører aldrig databasen direkte ud fra en opdagelse.

Denne fil har flere kolonner end den myndighedsafgrænsede `discover`,
fordi den globale opdagelse har mere at redegøre for: en forhåndsscore,
hvilke termer der bidrog, og om nummeret allerede findes i systemet.
`enqueue-manifest` (se `manifest_csv.read_manifest`) kræver kun
`accession_number` og `decision` og ignorerer ukendte kolonner, så denne
udvidede fil er læsbar af den EKSISTERENDE kommando uden ændringer der.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

from app.core.logging import get_logger

from .global_service import PrescoredHit

logger = get_logger(__name__)

__all__ = ["COLUMNS", "write_global_manifest"]

COLUMNS: Sequence[str] = (
    "accession_number",
    "title",
    "authority",
    "status",
    "document_type",
    "published_date",
    "eli_url",
    "source_query",
    "prescore",
    "prescore_classification",
    "matched_terms",
    "already_known",
    "discovered_at",
    "decision",
)

_ENCODING = "utf-8-sig"


def write_global_manifest(
    path: Path | str,
    hits: Iterable[PrescoredHit],
    *,
    header_comment: str | None = None,
) -> int:
    """Skriver det globale manifest. Returnerer antal linjer.

    `decision` kommer fra den enkelte kandidats egen forhåndsvurdering
    (`PrescoredHit.decision`) — IKKE en enkelt værdi for hele filen, i
    modsætning til den myndighedsafgrænsede `manifest_csv.write_manifest`.
    Det er selve pointen med at have en forhåndsvurdering.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    rows = sorted(hits, key=lambda p: p.hit.accession_number)

    with target.open("w", encoding=_ENCODING, newline="") as handle:
        if header_comment:
            handle.write(f"# {header_comment}\n")
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for prescored in rows:
            hit = prescored.hit
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
                    "prescore": prescored.prescore,
                    "prescore_classification": prescored.prescore_classification,
                    "matched_terms": "; ".join(prescored.matched_terms),
                    "already_known": "ja" if prescored.already_known else "",
                    "discovered_at": hit.discovered_at.isoformat(timespec="seconds"),
                    "decision": prescored.decision,
                }
            )

    logger.info(
        "discovery.global_manifest.written", extra={"path": str(target), "rows": len(rows)}
    )
    return len(rows)

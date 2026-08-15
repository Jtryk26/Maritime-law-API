#!/usr/bin/env python3
"""Genberegn prescoren for et discovery-manifest med den rigtige relevansmotor.

Formål
======
``triage_review.py`` retter beslutningerne i det manifest, vi allerede har.
Det retter ikke årsagen. Årsagen ligger i ``config/maritime_keywords.yaml``,
og en ændring dér skal kunne måles, før den anvendes — ellers flytter vi bare
fejlen til næste ``discover-global``-kørsel.

Dette script indlæser ``KeywordRelevanceEngine`` med en valgt
konfigurationsfil og genberegner scoren for hver linje i manifestet ud fra
titel + myndighed (nøjagtigt det grundlag ``global_service.py`` bruger til
forhåndsvurderingen — ingen brødtekst). Resultatet sammenholdes med den
``prescore``, der står i CSV'en.

Arbejdsgang::

    # 1. Nulpunkt med den nuværende konfiguration
    python3 scripts/rescore_manifest.py manifests/discovery-global.csv \
        --out /tmp/rescore-foer.csv

    # 2. Flet ændringerne fra config/maritime_keywords.additions.yaml ind
    #    i config/maritime_keywords.yaml, og mål igen
    python3 scripts/rescore_manifest.py manifests/discovery-global.csv \
        --out /tmp/rescore-efter.csv --changed-only

Kør fra ``backend/`` eller med ``PYTHONPATH=backend``, så ``app`` kan
importeres.

Note om koblingen: scriptet konstruerer ikke en ``NormalizedDocument``, men
et lille objekt med netop de fire ting, motoren læser (``title``,
``authority``, ``content``, ``metadata_text()``). Det holder værktøjet
uafhængigt af ændringer i dokumentmodellens konstruktør.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class _ScoringInput:
    """Minimalt dokument — kun det, KeywordRelevanceEngine.classify() læser."""

    title: str
    authority: str = ""
    content: str = ""
    document_type: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def metadata_text(self) -> str:
        parts = [self.document_type, *(str(v) for v in self.metadata.values())]
        return " ".join(part for part in parts if part)


def read_rows(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    lines = list(itertools.dropwhile(lambda line: line.lstrip().startswith("#"), lines))
    return [dict(row) for row in csv.DictReader(lines)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Sti til maritime_keywords.yaml (standard: motorens egen indstilling).",
    )
    parser.add_argument(
        "--only-decision",
        action="append",
        default=None,
        metavar="VÆRDI",
        help="Begræns til bestemte decision-værdier. Kan gentages.",
    )
    parser.add_argument("--changed-only", action="store_true", help="Vis kun ændrede scores.")
    parser.add_argument("--limit", type=int, default=40, help="Antal linjer i konsoloutput.")
    parser.add_argument("--out", type=Path, default=None, help="Skriv fuldt resultat som CSV.")
    args = parser.parse_args(argv)

    try:
        from app.services.relevance.keyword_engine import KeywordRelevanceEngine
    except ImportError as exc:  # pragma: no cover - afhænger af kørselsmiljø
        parser.error(
            f"Kunne ikke importere backend-koden ({exc}). "
            "Kør fra backend/ eller sæt PYTHONPATH=backend."
        )

    engine = KeywordRelevanceEngine(config_path=args.config) if args.config else KeywordRelevanceEngine()

    rows = read_rows(args.manifest)
    if args.only_decision:
        wanted = {value.strip().lower() for value in args.only_decision}
        rows = [r for r in rows if (r.get("decision") or "").strip().lower() in wanted]

    results = []
    for row in rows:
        result = engine.classify(
            _ScoringInput(
                title=row.get("title", ""),
                authority=row.get("authority", ""),
                document_type=row.get("document_type", ""),
            )
        )
        try:
            old = int(row.get("prescore") or 0)
        except ValueError:
            old = 0
        results.append(
            {
                "accession_number": row.get("accession_number", ""),
                "authority": row.get("authority", ""),
                "decision": row.get("decision", ""),
                "old_prescore": old,
                "new_prescore": result.score,
                "delta": result.score - old,
                "old_classification": row.get("prescore_classification", ""),
                "new_classification": result.classification,
                "title_floor": result.title_floor_applied,
                "matched_terms": "; ".join(result.matched_terms[:8]),
                "title": row.get("title", ""),
            }
        )

    shown = [r for r in results if r["delta"] != 0] if args.changed_only else results
    shown.sort(key=lambda r: r["delta"], reverse=True)

    print(f"Manifest : {args.manifest}   ({len(rows)} rækker vurderet)")
    print(f"Ændrede scores: {sum(1 for r in results if r['delta'] != 0)}")
    print()
    print(f"{'accession':<14}{'før':>5}{'efter':>7}{'delta':>7}  {'klassifikation':<22}titel")
    for result in shown[: args.limit]:
        transition = f"{result['old_classification']} -> {result['new_classification']}"
        print(
            f"{result['accession_number']:<14}{result['old_prescore']:>5}"
            f"{result['new_prescore']:>7}{result['delta']:>+7}  {transition:<22}"
            f"{result['title'][:70]}"
        )
    if len(shown) > args.limit:
        print(f"... {len(shown) - args.limit} flere (se --out).")

    print()
    print("Ny klassifikation fordelt:")
    for label, count in Counter(r["new_classification"] for r in results).most_common():
        print(f"  {label:<16}{count:>6}")

    if args.out:
        with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)
        print(f"\nSkrevet: {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

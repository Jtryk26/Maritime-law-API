"""Fra søgelog til facitliste — den menneskelige del.

Et evalueringssæt kan ikke skrives af maskinen. Kun en fagperson kan
afgøre, om *Bekendtgørelse om redningsmidler i handelsskibe* er det, en
maskinmester skulle have haft, da han søgte efter "livbåde".

Arbejdsgangen følger samme mønster som `discover` → CSV → gennemgang →
`enqueue-manifest`, fordi det mønster allerede har vist sig at virke her:

    evaluate scaffold      → CSV med kandidater pr. søgning
    (menneskelig gennemgang, marker relevant = ja/nej)
    evaluate import-csv    → YAML-evalueringssæt
    evaluate run           → tallene

Søgningerne kan komme fra søgeloggen — altså det, brugerne faktisk har
spurgt om — hvilket er langt bedre end søgninger, en udvikler fandt på.

Pooling og dens skævhed
=======================
Kandidaterne samles fra ALLE tre søgetilstande, ikke kun fra én. Det er
ikke en bekvemmelighed: bygges facitlisten kun af det, den leksikalske
søgning fandt, vil den semantiske søgning per definition aldrig kunne
vise sin værdi, og målingen ville bekræfte det, den skulle afprøve.

Skævheden forsvinder ikke helt. Et dokument, som INGEN af tilstandene
fandt, kommer ikke i CSV'en og kan derfor aldrig markeres relevant.
Recall måles altså mod "det de tre tilstande tilsammen fandt", ikke mod
sandheden. Det er samme begrænsning som TREC's pooling, og den skal stå
i enhver rapport. Modvægten er at hæve `--candidates` og at lade
gennemgangen tilføje dokumenter i hånden.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services.search import (
    QueryLogService,
    SearchQuery,
    get_search_backend,
    resolve_search_mode,
)

from .base import EvalQuery, EvalSet

logger = get_logger(__name__)

__all__ = ["CANDIDATE_COLUMNS", "scaffold_candidates", "read_reviewed_csv", "AFFIRMATIVE"]

#: Faste kolonner i fast rækkefølge, så to udtræk kan sammenlignes i en git-diff.
CANDIDATE_COLUMNS = [
    "query",
    "source_id",
    "title",
    "authority",
    "status",
    "found_by",
    "best_rank",
    "relevant",
    "notes",
]

#: Værdier der læses som "ja" i `relevant`-kolonnen. Bevidst rummelig:
#: filen udfyldes i Excel af et menneske, ikke af en parser.
AFFIRMATIVE = frozenset({"ja", "j", "yes", "y", "true", "1", "x", "relevant"})


@dataclass(slots=True)
class Candidate:
    """Ét dokument foreslået til gennemgang for én søgning."""

    query: str
    source_id: str
    title: str
    authority: str
    status: str
    found_by: set[str]
    best_rank: int

    def to_row(self) -> dict[str, str]:
        return {
            "query": self.query,
            "source_id": self.source_id,
            "title": self.title,
            "authority": self.authority,
            "status": self.status,
            "found_by": "+".join(sorted(self.found_by)),
            "best_rank": str(self.best_rank),
            "relevant": "",  # udfyldes i hånden
            "notes": "",
        }


def queries_from_search_log(session: Session, *, limit: int, include_empty: bool) -> list[str]:
    """De søgninger brugerne faktisk har stillet.

    `include_empty` tager også dem uden resultat med. De er de mest
    interessante at få en facitliste på: enten mangler materialet, eller
    også finder søgningen ikke noget, den burde.
    """
    service = QueryLogService(session)
    entries = service.popular(limit=limit)
    if include_empty:
        seen = {e.query_hash for e in entries}
        entries += [e for e in service.without_results(limit=limit) if e.query_hash not in seen]
    return [e.query_text for e in entries][:limit]


def scaffold_candidates(
    session: Session,
    queries: list[str],
    *,
    modes: list[str],
    candidates_per_mode: int = 10,
) -> list[Candidate]:
    """Samler kandidater til gennemgang på tværs af søgetilstande."""
    rows: list[Candidate] = []

    for query_text in queries:
        pooled: dict[str, Candidate] = {}

        for mode in modes:
            effective_mode, _ = resolve_search_mode(session, mode)
            backend = get_search_backend(session, effective_mode)
            results = backend.search(
                session,
                SearchQuery(
                    q=query_text,
                    mode=effective_mode,
                    page=1,
                    page_size=candidates_per_mode,
                ),
            )

            for rank, hit in enumerate(results.hits, start=1):
                document = hit.document
                existing = pooled.get(document.source_id)
                if existing is None:
                    pooled[document.source_id] = Candidate(
                        query=query_text,
                        source_id=document.source_id,
                        title=document.title,
                        authority=document.authority or "",
                        status=document.status or "",
                        found_by={mode},
                        best_rank=rank,
                    )
                else:
                    existing.found_by.add(mode)
                    existing.best_rank = min(existing.best_rank, rank)

        # Sortér, så det den, der gennemgår filen, mest sandsynligt vil
        # markere som relevant, står øverst.
        rows.extend(sorted(pooled.values(), key=lambda c: (c.best_rank, c.source_id)))

    return rows


def write_candidate_csv(candidates: list[Candidate], path: Path | str) -> Path:
    """Skriver gennemgangsfilen.

    UTF-8 MED BOM: filen åbnes typisk i Excel, som ellers viser
    "Søfartsstyrelsen" forkert. Samme valg som discovery-manifestet.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(candidate.to_row())

    return file_path


def read_reviewed_csv(
    path: Path | str,
    *,
    corpus: str,
    synthetic: bool = False,
    description: str = "",
    keep_unmarked_as_negative_control: bool = True,
) -> EvalSet:
    """Læser den gennemgåede CSV og bygger et evalueringssæt.

    En søgning, hvor intet er markeret relevant, bliver til en negativ
    kontrol — men kun hvis `keep_unmarked_as_negative_control` er sat.
    Ellers udelades den. Forskellen er vigtig: "her findes ikke noget" og
    "det nåede jeg ikke at gennemgå" er ikke det samme, og et sæt der
    forveksler dem, straffer søgemaskinen for menneskets manglende tid.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Gennemgangsfilen findes ikke: {file_path}")

    order: list[str] = []
    relevant: dict[str, set[str]] = {}
    notes: dict[str, list[str]] = {}
    marked_any: set[str] = set()

    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {"query", "source_id", "relevant"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{file_path}: mangler kolonnerne {', '.join(sorted(missing))}. "
                f"Forventede: {', '.join(CANDIDATE_COLUMNS)}"
            )

        for row in reader:
            query_text = (row.get("query") or "").strip()
            if not query_text:
                continue
            if query_text not in relevant:
                order.append(query_text)
                relevant[query_text] = set()
                notes[query_text] = []

            note = (row.get("notes") or "").strip()
            if note:
                notes[query_text].append(note)

            marker = (row.get("relevant") or "").strip().lower()
            if not marker:
                continue
            marked_any.add(query_text)
            if marker in AFFIRMATIVE:
                source_id = (row.get("source_id") or "").strip()
                if source_id:
                    relevant[query_text].add(source_id)

    queries: list[EvalQuery] = []
    for query_text in order:
        hits = relevant[query_text]
        if not hits:
            if query_text not in marked_any:
                # Slet ikke gennemgået — udelades frem for at blive en
                # negativ kontrol, systemet så ville blive målt på.
                logger.warning(
                    "evaluation.unreviewed_query", extra={"query": query_text}
                )
                continue
            if not keep_unmarked_as_negative_control:
                continue
        queries.append(
            EvalQuery(
                query=query_text,
                relevant=hits,
                notes="; ".join(dict.fromkeys(notes[query_text]))[:500],
            )
        )

    if not queries:
        raise ValueError(
            f"{file_path}: ingen søgninger var markeret. Udfyld 'relevant'-kolonnen "
            "med ja/nej, før filen importeres."
        )

    return EvalSet(
        queries=queries,
        corpus=corpus,
        synthetic=synthetic,
        description=description,
    )

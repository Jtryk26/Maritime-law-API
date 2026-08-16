"""Evalueringssættets format.

Et evalueringssæt er en YAML-fil med søgninger og facitlister::

    corpus: fixture
    synthetic: true
    queries:
      - query: "livbåde"
        intent: "Finder systemet reglerne om redningsmidler, når brugeren
                 siger livbåd og loven siger redningsbåd?"
        relevant:
          - FIXT-BEK-2022-0450
        notes: "Ordet 'livbåd' står ikke i noget dokument."

Hvorfor en fil og ikke en tabel i databasen: facitlisten er et
menneskeligt arbejde, den skal gennemgås af en fagperson, og den skal
kunne ses i en git-diff når nogen ændrer en vurdering. Samme betragtning
som CSV'en mellem `discover` og køen.

Dokumenter identificeres ved `source_id` — for Retsinformation er det
accessionsnummeret. Det er den eneste nøgle der overlever en
genopbygning af databasen.

Negative kontroller
===================
En søgning med tom `relevant`-liste er en **negativ kontrol**: den skal
IKKE give resultater. `folkeskole` mod en maritim samling er den
tydeligste. De indgår ikke i recall-gennemsnittet — de har ingen facit
at ramme — men tælles for sig, fordi et system der svarer på alt er lige
så ubrugeligt som et der ikke svarer på noget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = ["EvalQuery", "EvalSet", "EvalSetError", "load_eval_set", "save_eval_set"]


class EvalSetError(ValueError):
    """Evalueringssættet kunne ikke læses eller er ikke gyldigt."""


@dataclass(slots=True)
class EvalQuery:
    """Én søgning med sin facitliste."""

    query: str
    #: source_id'er der SKAL findes. Tom = negativ kontrol.
    relevant: set[str] = field(default_factory=set)
    #: Hvorfor søgningen er med. Hjælper den næste, der gennemgår sættet.
    intent: str = ""
    notes: str = ""
    #: Frit mærkat til at gruppere, f.eks. "ordforråd", "eksakt-term".
    tags: list[str] = field(default_factory=list)

    @property
    def is_negative_control(self) -> bool:
        return not self.relevant

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"query": self.query}
        if self.intent:
            data["intent"] = self.intent
        data["relevant"] = sorted(self.relevant)
        if self.tags:
            data["tags"] = list(self.tags)
        if self.notes:
            data["notes"] = self.notes
        return data


@dataclass(slots=True)
class EvalSet:
    """En samling søgninger med facit."""

    queries: list[EvalQuery]
    corpus: str = "unknown"
    #: Sand hvis facitlisten gælder syntetiske fixturdokumenter. Skal
    #: fremgå af enhver rapport: tal målt på 15 konstruerede dokumenter
    #: siger intet om 2.900 rigtige.
    synthetic: bool = False
    description: str = ""
    source_path: Path | None = None

    @property
    def graded(self) -> list[EvalQuery]:
        """Søgninger med facit — dem der kan måles recall på."""
        return [q for q in self.queries if not q.is_negative_control]

    @property
    def negative_controls(self) -> list[EvalQuery]:
        return [q for q in self.queries if q.is_negative_control]

    @property
    def all_relevant_ids(self) -> set[str]:
        ids: set[str] = set()
        for query in self.queries:
            ids |= query.relevant
        return ids


def load_eval_set(path: Path | str) -> EvalSet:
    """Læser og validerer et evalueringssæt."""
    file_path = Path(path)
    if not file_path.exists():
        raise EvalSetError(f"Evalueringssættet findes ikke: {file_path}")

    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise EvalSetError(f"Ugyldig YAML i {file_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise EvalSetError(f"{file_path}: forventede et objekt på øverste niveau.")

    entries = raw.get("queries")
    if not isinstance(entries, list) or not entries:
        raise EvalSetError(f"{file_path}: 'queries' mangler eller er tom.")

    queries: list[EvalQuery] = []
    seen: set[str] = set()

    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise EvalSetError(f"{file_path}: post {index} er ikke et objekt.")

        text = str(entry.get("query", "")).strip()
        if not text:
            raise EvalSetError(f"{file_path}: post {index} mangler 'query'.")
        if text.lower() in seen:
            # To ens søgninger ville tælle dobbelt i gennemsnittet og
            # dermed vægte netop det spørgsmål tungere end alle andre.
            raise EvalSetError(f"{file_path}: søgningen {text!r} optræder mere end én gang.")
        seen.add(text.lower())

        relevant_raw = entry.get("relevant") or []
        if not isinstance(relevant_raw, list):
            raise EvalSetError(f"{file_path}: 'relevant' for {text!r} skal være en liste.")
        relevant = {str(v).strip() for v in relevant_raw if str(v).strip()}

        queries.append(
            EvalQuery(
                query=text,
                relevant=relevant,
                intent=str(entry.get("intent", "") or "").strip(),
                notes=str(entry.get("notes", "") or "").strip(),
                tags=[str(t) for t in (entry.get("tags") or [])],
            )
        )

    return EvalSet(
        queries=queries,
        corpus=str(raw.get("corpus", "unknown")),
        synthetic=bool(raw.get("synthetic", False)),
        description=str(raw.get("description", "") or ""),
        source_path=file_path,
    )


def save_eval_set(eval_set: EvalSet, path: Path | str) -> Path:
    """Skriver et evalueringssæt. Bruges af `evaluate import-csv`."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "corpus": eval_set.corpus,
        "synthetic": eval_set.synthetic,
        "description": eval_set.description,
        "queries": [q.to_json() for q in eval_set.queries],
    }
    file_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    return file_path

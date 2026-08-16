"""Måletal for søgekvalitet.

Definitionerne står her alene og uden afhængigheder, så de kan
efterprøves i hånden. Det er med vilje: et måletal, ingen kan regne
efter, er værre end intet måletal, fordi det bliver troet.

Relevans er binær i Version 1. Et dokument er enten noget, den der
søgte skulle have haft, eller ikke. Graderet relevans ("dette er det
vigtigste, dette er også brugbart") kræver flere vurderinger pr.
søgning end et lille hold kan nå at lave ordentligt, og en dårligt
graderet facitliste måler støj med flere decimaler.

Alle funktioner tager `retrieved` som en rangordnet liste af
dokument-id'er (bedste først) og `relevant` som en mængde. Dubletter i
`retrieved` er kaldernes ansvar; søgelaget leverer ikke dubletter.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = [
    "recall_at_k",
    "precision_at_k",
    "reciprocal_rank",
    "ndcg_at_k",
    "first_relevant_rank",
    "mean",
]


def _hits(retrieved: Sequence[str], relevant: set[str], k: int) -> int:
    return sum(1 for doc in retrieved[:k] if doc in relevant)


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Hvor stor en del af de rigtige dokumenter kom med i top-k.

    Det vigtigste tal for en juridisk søgning: et overset dokument er en
    regel, brugeren ikke ved findes. En bruger kan skimme ti resultater;
    han kan ikke gætte det ellevte.

    Udefineret uden facit — returnerer 0,0, og kalderen skal undlade at
    tælle den slags søgninger med. :func:`mean` gør det ikke for dig.
    """
    if not relevant:
        return 0.0
    return _hits(retrieved, relevant, k) / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Hvor stor en del af top-k var rigtige.

    Bemærk loftet: er der kun to rigtige dokumenter i hele samlingen, kan
    P@10 aldrig overstige 0,2. Tallet er derfor kun sammenligneligt
    mellem søgetilstande på det SAMME sæt, aldrig på tværs af sæt med
    forskelligt antal rigtige svar.
    """
    if k <= 0:
        return 0.0
    return _hits(retrieved, relevant, k) / k


def first_relevant_rank(retrieved: Sequence[str], relevant: set[str]) -> int | None:
    """1-indekseret plads for det første rigtige dokument. None hvis intet."""
    for position, doc in enumerate(retrieved, start=1):
        if doc in relevant:
            return position
    return None


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    """1 / pladsen for det første rigtige træf. 0 hvis der ikke var noget.

    Måler "hvor hurtigt fik brugeren fat i noget brugbart". Et træf på
    plads 1 giver 1,0, plads 2 giver 0,5, plads 10 giver 0,1.
    """
    rank = first_relevant_rank(retrieved, relevant)
    return 1.0 / rank if rank else 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Normaliseret discounted cumulative gain over binær relevans.

    Til forskel fra recall belønner nDCG at de rigtige dokumenter ligger
    ØVERST, ikke bare at de er med::

        DCG@k  = Σ  rel_i / log2(i + 1)      for i = 1..k
        IDCG@k = Σ  1     / log2(i + 1)      for i = 1..min(k, |relevant|)
        nDCG   = DCG / IDCG

    Med ét rigtigt dokument på plads 1 er nDCG 1,0; på plads 3 er det
    1/log2(4) = 0,5.
    """
    if not relevant or k <= 0:
        return 0.0

    dcg = sum(
        1.0 / math.log2(position + 1)
        for position, doc in enumerate(retrieved[:k], start=1)
        if doc in relevant
    )
    ideal = sum(
        1.0 / math.log2(position + 1)
        for position in range(1, min(k, len(relevant)) + 1)
    )
    return dcg / ideal if ideal else 0.0


def mean(values: Sequence[float]) -> float:
    """Gennemsnit. Tom liste giver 0,0 frem for en exception."""
    return sum(values) / len(values) if values else 0.0

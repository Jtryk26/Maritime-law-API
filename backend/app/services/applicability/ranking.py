"""Rangering af afgørelser.

Sorteringen er en leksikografisk nøgle, ikke en vægtet sum. En vægtet sum kan
lade tre svage signaler skubbe et "gælder ikke" over et "gælder".

``rank_score`` beregnes stadig og vises i brugerfladen, men afgør kun
rækkefølgen inden for samme afgørelse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import ApplicabilityResult, Verdict

__all__ = ["VERDICT_ORDER", "RankedResult", "rank_results", "group_by_verdict"]

#: Manuel gennemgang rangeres over "gælder ikke" med vilje: et spørgsmål,
#: motoren ikke kunne afgøre, må ikke ende under de afviste regler.
VERDICT_ORDER: dict[Verdict, int] = {
    Verdict.APPLIES: 0,
    Verdict.POSSIBLY_APPLIES: 1,
    Verdict.NEEDS_MANUAL_REVIEW: 2,
    Verdict.DOES_NOT_APPLY: 3,
}

_VERDICT_POINTS: dict[Verdict, int] = {
    Verdict.APPLIES: 600,
    Verdict.POSSIBLY_APPLIES: 400,
    Verdict.NEEDS_MANUAL_REVIEW: 250,
    Verdict.DOES_NOT_APPLY: 0,
}

#: Lavere bindingness = stærkere retskilde.
_BINDINGNESS_POINTS: dict[int, int] = {1: 120, 2: 90, 3: 50, 4: 20}


@dataclass(slots=True)
class RankedResult:
    result: ApplicabilityResult
    rank: int
    rank_score: float
    components: list[tuple[str, float]] = field(default_factory=list)


def rank_results(
    results: list[ApplicabilityResult],
    *,
    drop_non_applicable: bool = False,
    boosts: dict[int, float] | None = None,
) -> list[RankedResult]:
    boosts = boosts or {}
    pool = [r for r in results if not (drop_non_applicable and r.verdict is Verdict.DOES_NOT_APPLY)]

    def sort_key(result: ApplicabilityResult) -> tuple:
        # Nyeste ikrafttrædelse først; en regel uden dato lægges bagest.
        recency = -result.in_force_from.toordinal() if result.in_force_from else 0
        return (
            VERDICT_ORDER[result.verdict],
            result.bindingness,
            -result.specificity_score,
            -result.confidence,
            recency,
            result.rule_id or 0,
            result.rule_ref,
        )

    ordered = sorted(pool, key=sort_key)

    ranked: list[RankedResult] = []
    for position, result in enumerate(ordered, start=1):
        boost = boosts.get(result.rule_id or -1, 0.0)
        components = [
            ("verdict", float(_VERDICT_POINTS[result.verdict])),
            ("bindingness", float(_BINDINGNESS_POINTS.get(result.bindingness, 0))),
            ("specificity", float(result.specificity_score * 8)),
            ("confidence", float(result.confidence) * 0.5),
            ("boost", float(boost)),
        ]
        ranked.append(
            RankedResult(
                result=result,
                rank=position,
                rank_score=round(sum(value for _, value in components), 1),
                components=components,
            )
        )
    return ranked


def group_by_verdict(ranked: list[RankedResult]) -> dict[Verdict, list[RankedResult]]:
    groups: dict[Verdict, list[RankedResult]] = {verdict: [] for verdict in VERDICT_ORDER}
    for entry in ranked:
        groups[entry.result.verdict].append(entry)
    return groups

"""Kører et evalueringssæt mod søgemaskinen.

Hver søgning køres i hver tilstand — leksikalsk, semantisk, hybrid — mod
samme database og samme facitliste. Det er hele pointen: de tre tal skal
kunne sammenlignes, og det kan de kun, hvis alt andet er ens.

Hvad der IKKE måles her
=======================
Ikke svartid. Ikke hukommelsesforbrug. En evaluering der blander
kvalitet og hastighed inviterer til at bytte det ene for det andet uden
at nogen bemærker det. Skal svartider måles, hører de til et andet sted.

Dokumenter identificeres ved `source_id`, ikke ved databasens
løbenummer, så et evalueringssæt overlever en genopbygning af databasen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.search import SearchQuery, get_search_backend, resolve_search_mode

from .base import EvalQuery, EvalSet
from .metrics import (
    first_relevant_rank,
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

logger = get_logger(__name__)

__all__ = ["EvaluationRunner", "QueryOutcome", "ModeSummary", "EvaluationReport"]


@dataclass(slots=True)
class QueryOutcome:
    """Resultatet af én søgning i én tilstand."""

    query: str
    mode: str
    #: Faktisk leveret tilstand. Afviger den, er målingen ikke det, den ser ud til.
    effective_mode: str
    retrieved: list[str]
    relevant: set[str]
    recall: float = 0.0
    precision: float = 0.0
    reciprocal_rank: float = 0.0
    ndcg: float = 0.0
    first_hit_rank: int | None = None
    total_results: int = 0

    @property
    def is_negative_control(self) -> bool:
        return not self.relevant

    @property
    def negative_control_passed(self) -> bool:
        """Negativ kontrol bestås ved slet ikke at svare."""
        return self.is_negative_control and self.total_results == 0

    @property
    def missed(self) -> list[str]:
        """De rigtige dokumenter der ikke kom med i top-k."""
        return sorted(self.relevant - set(self.retrieved))


@dataclass(slots=True)
class ModeSummary:
    """Samlede tal for én søgetilstand."""

    mode: str
    k: int
    queries: int = 0
    recall: float = 0.0
    precision: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0
    #: Søgninger hvor mindst ét rigtigt dokument blev fundet.
    queries_with_any_hit: int = 0
    #: Søgninger hvor ALT det rigtige blev fundet.
    queries_fully_covered: int = 0
    negative_controls: int = 0
    negative_controls_passed: int = 0
    outcomes: list[QueryOutcome] = field(default_factory=list)
    #: Sat hvis den ønskede tilstand ikke kunne leveres for alle søgninger.
    downgraded: bool = False

    def to_json(self) -> dict:
        return {
            "mode": self.mode,
            "k": self.k,
            "queries": self.queries,
            "recall_at_k": round(self.recall, 4),
            "precision_at_k": round(self.precision, 4),
            "mrr": round(self.mrr, 4),
            "ndcg_at_k": round(self.ndcg, 4),
            "queries_with_any_hit": self.queries_with_any_hit,
            "queries_fully_covered": self.queries_fully_covered,
            "negative_controls": self.negative_controls,
            "negative_controls_passed": self.negative_controls_passed,
            "downgraded": self.downgraded,
        }


@dataclass(slots=True)
class EvaluationReport:
    """Hele evalueringen."""

    corpus: str
    synthetic: bool
    k: int
    summaries: list[ModeSummary]
    #: source_id'er i facitlisten der slet ikke findes i databasen.
    #: En facitliste der peger på noget uimporteret måler ingenting.
    missing_from_corpus: list[str] = field(default_factory=list)
    embedding_model: str | None = None
    embedding_semantic: bool | None = None

    def best(self, metric: str = "recall") -> ModeSummary | None:
        if not self.summaries:
            return None
        return max(self.summaries, key=lambda s: getattr(s, metric))

    def to_json(self) -> dict:
        return {
            "corpus": self.corpus,
            "synthetic": self.synthetic,
            "k": self.k,
            "embedding_model": self.embedding_model,
            "embedding_semantic": self.embedding_semantic,
            "missing_from_corpus": self.missing_from_corpus,
            "modes": [s.to_json() for s in self.summaries],
            "per_query": [
                {
                    "query": o.query,
                    "mode": o.mode,
                    "effective_mode": o.effective_mode,
                    "recall": round(o.recall, 4),
                    "ndcg": round(o.ndcg, 4),
                    "first_hit_rank": o.first_hit_rank,
                    "total_results": o.total_results,
                    "missed": o.missed,
                    "retrieved": o.retrieved,
                }
                for summary in self.summaries
                for o in summary.outcomes
            ],
        }


class EvaluationRunner:
    """Kører et evalueringssæt."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        k: int = 10,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.k = k

    # -- Kørsel -------------------------------------------------------------

    def run(self, eval_set: EvalSet, modes: list[str]) -> EvaluationReport:
        missing = self._missing_from_corpus(eval_set)
        if missing:
            # Ikke en fejl, men det SKAL siges: recall målt mod dokumenter,
            # der ikke findes i databasen, kan aldrig blive 1,0, og den der
            # læser rapporten ville lede efter fejlen i søgemaskinen.
            logger.warning(
                "evaluation.missing_documents",
                extra={"count": len(missing), "ids": ",".join(missing[:10])},
            )

        summaries = [self._run_mode(eval_set, mode) for mode in modes]

        model = None
        semantic = None
        if any(s.mode != "lexical" for s in summaries):
            try:
                from app.services.embedding import get_embedding_provider

                info = get_embedding_provider().info
                model, semantic = info.model, info.semantic
            except Exception:  # noqa: BLE001 - rapporten må ikke vælte på det
                pass

        return EvaluationReport(
            corpus=eval_set.corpus,
            synthetic=eval_set.synthetic,
            k=self.k,
            summaries=summaries,
            missing_from_corpus=missing,
            embedding_model=model,
            embedding_semantic=semantic,
        )

    def _run_mode(self, eval_set: EvalSet, mode: str) -> ModeSummary:
        summary = ModeSummary(mode=mode, k=self.k)

        for query in eval_set.queries:
            outcome = self._run_query(query, mode)
            summary.outcomes.append(outcome)

            if outcome.effective_mode != mode:
                summary.downgraded = True

            if outcome.is_negative_control:
                summary.negative_controls += 1
                if outcome.negative_control_passed:
                    summary.negative_controls_passed += 1
                continue

            summary.queries += 1
            if outcome.first_hit_rank is not None:
                summary.queries_with_any_hit += 1
            if not outcome.missed:
                summary.queries_fully_covered += 1

        graded = [o for o in summary.outcomes if not o.is_negative_control]
        summary.recall = mean([o.recall for o in graded])
        summary.precision = mean([o.precision for o in graded])
        summary.mrr = mean([o.reciprocal_rank for o in graded])
        summary.ndcg = mean([o.ndcg for o in graded])
        return summary

    def _run_query(self, query: EvalQuery, mode: str) -> QueryOutcome:
        effective_mode, _ = resolve_search_mode(self.session, mode)
        backend = get_search_backend(self.session, effective_mode)

        results = backend.search(
            self.session,
            SearchQuery(q=query.query, mode=effective_mode, page=1, page_size=self.k),
        )
        retrieved = [hit.document.source_id for hit in results.hits]

        return QueryOutcome(
            query=query.query,
            mode=mode,
            effective_mode=results.mode or effective_mode,
            retrieved=retrieved,
            relevant=set(query.relevant),
            recall=recall_at_k(retrieved, query.relevant, self.k),
            precision=precision_at_k(retrieved, query.relevant, self.k),
            reciprocal_rank=reciprocal_rank(retrieved, query.relevant),
            ndcg=ndcg_at_k(retrieved, query.relevant, self.k),
            first_hit_rank=first_relevant_rank(retrieved, query.relevant),
            total_results=results.total,
        )

    # -- Kontrol ------------------------------------------------------------

    def _missing_from_corpus(self, eval_set: EvalSet) -> list[str]:
        """Facit-id'er der ikke findes i databasen."""
        from sqlalchemy import select

        from app.models import Document

        wanted = eval_set.all_relevant_ids
        if not wanted:
            return []

        found = set(
            self.session.scalars(
                select(Document.source_id).where(Document.source_id.in_(wanted))
            ).all()
        )
        return sorted(wanted - found)

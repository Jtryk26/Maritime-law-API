"""Måling af søgekvalitet.

Uden et evalueringssæt er enhver påstand om at søgningen "finder de
rigtige dokumenter" et postulat. Dette lag gør påstanden til et tal, og
gør det muligt at se, om en ændring af model, vægte eller tærskel
faktisk gjorde det bedre.

    evaluate scaffold   → CSV med kandidater til menneskelig gennemgang
    evaluate import-csv → YAML-evalueringssæt
    evaluate run        → recall, præcision, MRR og nDCG pr. søgetilstand
"""

from .base import EvalQuery, EvalSet, EvalSetError, load_eval_set, save_eval_set
from .metrics import (
    first_relevant_rank,
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from .runner import EvaluationReport, EvaluationRunner, ModeSummary, QueryOutcome
from .scaffold import (
    AFFIRMATIVE,
    CANDIDATE_COLUMNS,
    Candidate,
    queries_from_search_log,
    read_reviewed_csv,
    scaffold_candidates,
    write_candidate_csv,
)

__all__ = [
    "AFFIRMATIVE",
    "CANDIDATE_COLUMNS",
    "Candidate",
    "EvalQuery",
    "EvalSet",
    "EvalSetError",
    "EvaluationReport",
    "EvaluationRunner",
    "ModeSummary",
    "QueryOutcome",
    "first_relevant_rank",
    "load_eval_set",
    "mean",
    "ndcg_at_k",
    "precision_at_k",
    "queries_from_search_log",
    "read_reviewed_csv",
    "recall_at_k",
    "reciprocal_rank",
    "save_eval_set",
    "scaffold_candidates",
    "write_candidate_csv",
]

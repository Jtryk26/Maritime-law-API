"""Arbejder der tømmer efterindlæsningskøen.

Arbejderen ejer ingen forretningslogik. Den:

1. reserverer en portion accessionsnumre (:mod:`manifest`),
2. lader :class:`ImportService` køre dem gennem den normale pipeline,
3. oversætter pr-dokument udfaldet til køstatus bag fencing token.

Hvorfor en portion ad gangen
============================
Én ``ImportService.run()`` pr. dokument ville give én ``import_runs``-
række pr. dokument og gøre importhistorikken ubrugelig. Portionen giver
én importkørsel pr. batch, og :attr:`ImportSummary.outcomes` fortæller
hvad der skete med hvert enkelt kilde-id.

Afbrudt kørsel
==============
Stopper importeren tidligt (for mange fejl i træk), står de resterende
reserverede poster uden udfald. De frigives eksplicit til PENDING uden
at bruge et forsøg, i stedet for at vente på at reservationen udløber.
"""

from __future__ import annotations

import socket
import os
import uuid
from dataclasses import dataclass, field
from typing import Callable

from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import BackfillStatus, ImportStatus
from app.services.backfill import manifest
from app.services.backfill.manifest import ClaimedItem
from app.services.categorization import get_categorization_engine
from app.services.importer import ImportService
from app.services.importer.service import Outcome
from app.services.relevance import get_relevance_engine
from app.services.retsinformation.base import SourceClient

logger = get_logger(__name__)

__all__ = ["BackfillResult", "default_worker_id", "run_backfill"]


#: Udfald der er endelige for kø-posten.
_TERMINAL_OUTCOMES = {
    Outcome.CREATED: BackfillStatus.COMPLETED,
    Outcome.UPDATED: BackfillStatus.COMPLETED,
    Outcome.UNCHANGED: BackfillStatus.COMPLETED,
    Outcome.REJECTED: BackfillStatus.REJECTED,
}


@dataclass(slots=True)
class BackfillResult:
    """Opsummering af en arbejderkørsel."""

    worker_id: str
    batches: int = 0
    claimed: int = 0
    completed: int = 0
    rejected: int = 0
    retry: int = 0
    failed: int = 0
    released: int = 0
    #: Poster hvor reservationen var tabt, da udfaldet skulle skrives.
    fence_breaches: int = 0
    import_run_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, int | str | list[int]]:
        return {
            "worker_id": self.worker_id,
            "batches": self.batches,
            "claimed": self.claimed,
            "completed": self.completed,
            "rejected": self.rejected,
            "retry": self.retry,
            "failed": self.failed,
            "released": self.released,
            "fence_breaches": self.fence_breaches,
            "import_run_ids": self.import_run_ids,
        }


def default_worker_id() -> str:
    """Stabilt nok til logning, unikt nok til at skelne processer."""
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def run_backfill(
    *,
    client: SourceClient,
    worker_id: str | None = None,
    batch_size: int = 25,
    max_batches: int | None = None,
    max_attempts: int = manifest.DEFAULT_MAX_ATTEMPTS,
    lease_minutes: int = manifest.DEFAULT_LEASE_MINUTES,
    session_factory: Callable = session_scope,
) -> BackfillResult:
    """Tømmer køen for poster, der kan behandles nu.

    Kørslen stopper når der ikke er flere poster at reservere. Poster i
    RETRY med fremtidig `next_attempt_at` behandles ikke — kør igen
    senere (cron, worker-container) for at tage dem.

    Args:
        client: Kildeklienten. Kalderen ejer og lukker den.
        batch_size: Antal accessionsnumre pr. importkørsel.
        max_batches: Stop efter dette antal portioner. None = tøm køen.
        max_attempts: Forsøg pr. post før den regnes endeligt mislykket.
        lease_minutes: Reservationens levetid.
    """
    worker = worker_id or default_worker_id()
    result = BackfillResult(worker_id=worker)

    logger.info(
        "backfill.worker.started",
        extra={
            "worker_id": worker,
            "batch_size": batch_size,
            "client": getattr(client, "kind", "ukendt"),
        },
    )

    while max_batches is None or result.batches < max_batches:
        with session_factory() as session:
            claimed = manifest.claim_batch(
                session,
                worker_id=worker,
                batch_size=batch_size,
                lease_minutes=lease_minutes,
            )

        if not claimed:
            logger.info("backfill.worker.queue_drained", extra={"worker_id": worker})
            break

        result.batches += 1
        result.claimed += len(claimed)
        _process_batch(
            claimed,
            client=client,
            result=result,
            max_attempts=max_attempts,
            session_factory=session_factory,
        )

    logger.info("backfill.worker.finished", extra={"worker_id": worker, **_counts(result)})
    return result


def _counts(result: BackfillResult) -> dict[str, int]:
    return {
        "batches": result.batches,
        "claimed": result.claimed,
        "completed": result.completed,
        "rejected": result.rejected,
        "retry": result.retry,
        "failed": result.failed,
        "released": result.released,
        "fence_breaches": result.fence_breaches,
    }


def _process_batch(
    claimed: list[ClaimedItem],
    *,
    client: SourceClient,
    result: BackfillResult,
    max_attempts: int,
    session_factory: Callable,
) -> None:
    """Kører én portion gennem importeren og opdaterer køen."""
    accessions = [item.accession_number for item in claimed]

    try:
        with session_factory() as session:
            service = ImportService(
                session,
                client=client,
                relevance_engine=get_relevance_engine(),
                categorization_engine=get_categorization_engine(),
            )
            summary = service.run(explicit_ids=accessions, trigger="backfill")
    except Exception as exc:
        # Importeren nåede ikke at behandle noget (f.eks. kunne
        # kildelisten ikke bygges). Hele portionen forsøges igen.
        logger.exception("backfill.batch.failed", extra={"count": len(claimed)})
        _finish_all(
            claimed,
            BackfillStatus.RETRY,
            error=f"{type(exc).__name__}: {exc}",
            result=result,
            max_attempts=max_attempts,
            session_factory=session_factory,
        )
        return

    result.import_run_ids.append(summary.import_run_id)
    outcomes = summary.outcome_map()

    for item in claimed:
        outcome = outcomes.get(item.accession_number)

        if outcome is None:
            # Ingen udfald: kørslen blev afbrudt, før posten blev nået.
            with session_factory() as session:
                if manifest.release(
                    session,
                    item,
                    reason=(
                        f"Importkørsel #{summary.import_run_id} blev afbrudt "
                        f"({summary.status}) før posten blev behandlet."
                    ),
                ):
                    result.released += 1
                else:
                    result.fence_breaches += 1
            continue

        if outcome.outcome is Outcome.FAILED:
            target = (
                BackfillStatus.RETRY if outcome.retryable else BackfillStatus.FAILED
            )
            error = f"{outcome.error_type}: {outcome.error}"
        else:
            target = _TERMINAL_OUTCOMES[outcome.outcome]
            error = None

        with session_factory() as session:
            written = manifest.finish(
                session,
                item,
                target,
                error=error,
                import_run_id=summary.import_run_id,
                max_attempts=max_attempts,
            )

        if written is None:
            result.fence_breaches += 1
        else:
            _tally(result, written)

    if summary.status == ImportStatus.FAILED.value:
        logger.warning(
            "backfill.batch.aborted",
            extra={
                "import_run_id": summary.import_run_id,
                "claimed": len(claimed),
                "released": result.released,
            },
        )


def _finish_all(
    claimed: list[ClaimedItem],
    status: BackfillStatus,
    *,
    error: str | None,
    result: BackfillResult,
    max_attempts: int,
    session_factory: Callable,
) -> None:
    for item in claimed:
        with session_factory() as session:
            written = manifest.finish(
                session, item, status, error=error, max_attempts=max_attempts
            )
        if written is None:
            result.fence_breaches += 1
        else:
            _tally(result, written)


def _tally(result: BackfillResult, status: BackfillStatus) -> None:
    if status is BackfillStatus.COMPLETED:
        result.completed += 1
    elif status is BackfillStatus.REJECTED:
        result.rejected += 1
    elif status is BackfillStatus.RETRY:
        result.retry += 1
    elif status is BackfillStatus.FAILED:
        result.failed += 1

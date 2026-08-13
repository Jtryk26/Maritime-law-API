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
En importkørsel kan ende som FAILED på to måder: kildelisten kunne ikke
bygges, eller for mange dokumenter fejlede i træk. Begge dele betyder,
at kilden er nede — ikke at netop disse poster er dårlige.

`ImportService.run()` *returnerer* i det tilfælde en FAILED-opsummering
uden udfald frem for at kaste. Behandles det som "posterne blev bare
ikke nået", frigives de til PENDING, reserveres straks igen, og
arbejderen kører i ring og fylder `import_runs` med fejlede kørsler.

Derfor: en FAILED-kørsel sætter portionens ubehandlede poster i RETRY
med ventetid — så de bruger et forsøg og til sidst opgives — og stopper
arbejderen. Køen er uændret gyldig; næste kørsel tager den op igen.
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
    #: Sat hvis arbejderen stoppede før køen var tom.
    stopped_early: str | None = None

    def as_dict(self) -> dict[str, int | str | list[int] | None]:
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
            "stopped_early": self.stopped_early,
        }


def default_worker_id() -> str:
    """Stabilt nok til logning, unikt nok til at skelne processer.

    Værtsnavnet afkortes, så det samlede id passer i kolonnen også på
    en maskine med et langt FQDN.
    """
    suffix = f"-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    hostname = socket.gethostname()[: manifest.ID_MAX_LENGTH - len(suffix)]
    return f"{hostname}{suffix}"


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
        lease_minutes: Reservationens levetid. Skal overstige
            behandlingstiden for en hel portion, ikke ét dokument.
    """
    if batch_size < 1:
        raise ValueError("batch_size skal være mindst 1.")
    if lease_minutes < 1:
        raise ValueError("lease_minutes skal være mindst 1.")
    if max_attempts < 1:
        raise ValueError("max_attempts skal være mindst 1.")
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches skal være mindst 1 eller None.")

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
        keep_going = _process_batch(
            claimed,
            client=client,
            result=result,
            max_attempts=max_attempts,
            session_factory=session_factory,
        )

        if not keep_going:
            # Kilden er nede. Fortsætter arbejderen, reserverer den blot
            # de næste poster og fejler dem på samme måde — i værste fald
            # i ring, hvis posterne kommer tilbage i køen.
            logger.warning(
                "backfill.worker.stopped_early",
                extra={"worker_id": worker, "reason": result.stopped_early},
            )
            break

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
) -> bool:
    """Kører én portion gennem importeren og opdaterer køen.

    Returns:
        True hvis arbejderen bør tage næste portion. False hvis kilden
        ser ud til at være nede, og der ikke er noget at vinde ved at
        fortsætte.
    """
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
        # Importeren kastede, før den nåede at behandle noget. Hele
        # portionen forsøges igen, og arbejderen stopper.
        logger.exception("backfill.batch.failed", extra={"count": len(claimed)})
        _finish_all(
            claimed,
            BackfillStatus.RETRY,
            error=f"{type(exc).__name__}: {exc}",
            result=result,
            max_attempts=max_attempts,
            session_factory=session_factory,
        )
        result.stopped_early = f"importen kastede: {type(exc).__name__}: {exc}"
        return False

    result.import_run_ids.append(summary.import_run_id)
    outcomes = summary.outcome_map()
    import_failed = summary.status == ImportStatus.FAILED.value

    for item in claimed:
        outcome = outcomes.get(item.accession_number)

        if outcome is None:
            _handle_missing_outcome(
                item,
                summary=summary,
                import_failed=import_failed,
                result=result,
                max_attempts=max_attempts,
                session_factory=session_factory,
            )
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

    if import_failed:
        logger.warning(
            "backfill.batch.aborted",
            extra={
                "import_run_id": summary.import_run_id,
                "claimed": len(claimed),
                "reason": summary.errors[0]["error"][:200] if summary.errors else "ukendt",
            },
        )
        result.stopped_early = (
            f"importkørsel #{summary.import_run_id} endte som FAILED"
        )
        return False

    return True


def _handle_missing_outcome(
    item: ClaimedItem,
    *,
    summary,
    import_failed: bool,
    result: BackfillResult,
    max_attempts: int,
    session_factory: Callable,
) -> None:
    """En reserveret post uden udfald i opsummeringen.

    Endte kørslen som FAILED, er kilden nede. Posten sættes i RETRY med
    ventetid, så den bruger et forsøg og til sidst opgives. Blev den blot
    ikke nået af andre grunde, frigives den uden at bruge et forsøg.
    """
    with session_factory() as session:
        if import_failed:
            written = manifest.finish(
                session,
                item,
                BackfillStatus.RETRY,
                error=(
                    f"Importkørsel #{summary.import_run_id} endte som "
                    f"{summary.status}; posten blev ikke behandlet."
                ),
                max_attempts=max_attempts,
            )
            if written is None:
                result.fence_breaches += 1
            else:
                _tally(result, written)
            return

        if manifest.release(
            session,
            item,
            reason=(
                f"Importkørsel #{summary.import_run_id} ({summary.status}) "
                "returnerede intet udfald for posten."
            ),
        ):
            result.released += 1
        else:
            result.fence_breaches += 1


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

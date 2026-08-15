"""Køstyring for historisk efterindlæsning.

Dette modul ejer *tilstanden* i køen. Det henter ingen dokumenter og
kender ikke importpipelinen; det uddeler og inddrager reservationer.

Reservationsmodellen
====================
En arbejder kalder :func:`claim_batch` og får et antal
:class:`ClaimedItem` tilbage. Hver reservation har:

* ``claim_token`` — unikt pr. reservation.
* ``lease_expires_at`` — hvornår reservationen bortfalder.

Bortfalder reservationen, må en anden arbejder tage posten. Den første
arbejder kan stadig være midt i en langsom XML-hentning, så *alle*
senere statusskrivninger går gennem :func:`finish` / :func:`release`,
som har ``claim_token`` i WHERE-klausulen. Rammer en sådan skrivning nul
rækker, har arbejderen mistet posten, og skrivningen droppes.

Hvorfor det er tilstrækkeligt
=============================
Dokumentskrivningen i :class:`DocumentRepository` er indholds-hashet:
importeres samme tekst igen, oprettes ingen ny version. To arbejdere,
der behandler samme accessionsnummer, giver derfor ikke dubletter i
``document_versions`` — den anden får UNCHANGED. Fencing token beskytter
*køens* tilstand, ikke dokumenttabellerne, fordi dokumenttabellerne
allerede er idempotente.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import BackfillManifestItem, BackfillStatus

logger = get_logger(__name__)

__all__ = [
    "ClaimedItem",
    "DEFAULT_LEASE_MINUTES",
    "DEFAULT_MAX_ATTEMPTS",
    "ID_MAX_LENGTH",
    "backoff_delay",
    "claim_batch",
    "enqueue",
    "failed_items",
    "finish",
    "pending_accessions",
    "queue_counts",
    "release",
    "reset",
]

#: Reservationens levetid. Hele portionen reserveres på én gang, så
#: levetiden skal overstige behandlingstiden for *hele* portionen —
#: inklusive kildens rate limiting og de interne genforsøg i
#: ProductionRetsinformationClient — ikke blot ét dokuments hentning.
#: Er den for kort, stjæler arbejdere poster fra hinanden under normal
#: drift, og resultaterne droppes af fencing token.
DEFAULT_LEASE_MINUTES = 20

#: Antal forsøg før en post regnes som endeligt mislykket.
DEFAULT_MAX_ATTEMPTS = 3

#: Ventetid før første nye forsøg. Tredobles pr. forsøg.
_BASE_BACKOFF_MINUTES = 5
_MAX_BACKOFF_MINUTES = 6 * 60

#: Længste `worker_id` og `claim_token` kolonnerne kan rumme.
#: PostgreSQL afviser for lange værdier; SQLite håndhæver det ikke, så
#: afkortningen sker i koden frem for at blive opdaget i produktion.
ID_MAX_LENGTH = 64


@dataclass(slots=True, frozen=True)
class ClaimedItem:
    """En reserveret kø-post.

    `token` skal medbringes til enhver efterfølgende statusskrivning.
    """

    accession_number: str
    token: str
    attempt: int
    lease_expires_at: datetime


#: Felter der beskriver en *aktiv* reservation. Ryddes ved enhver
#: overgang til en tilstand, hvor posten ikke er under behandling —
#: ellers ser en PENDING- eller RETRY-række ud som om en arbejder
#: stadig er i gang med den.
_RESERVATION_FIELDS: dict[str, None] = {
    "claim_token": None,
    "worker_id": None,
    "lease_expires_at": None,
    "processing_started_at": None,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def backoff_delay(attempt: int) -> timedelta:
    """Eksponentiel ventetid: 5 min, 15 min, 45 min ... med loft."""
    minutes = _BASE_BACKOFF_MINUTES * (3 ** max(attempt - 1, 0))
    return timedelta(minutes=min(minutes, _MAX_BACKOFF_MINUTES))


# ---------------------------------------------------------------------------
# Opfyldning
# ---------------------------------------------------------------------------


def _requeue_item(item: BackfillManifestItem) -> None:
    """Sætter en terminal post tilbage til PENDING, som om den var ny.

    Alt fra det tidligere forløb ryddes, ellers peger completed_at og
    import_run_id på en kørsel, der intet har med det nye forsøg at gøre.
    """
    item.status = BackfillStatus.PENDING.value
    item.attempt_count = 0
    item.last_error = None
    item.next_attempt_at = None
    item.completed_at = None
    item.import_run_id = None
    for field_name, value in _RESERVATION_FIELDS.items():
        setattr(item, field_name, value)


def enqueue(
    session: Session,
    accession_numbers: Iterable[str],
    *,
    source_tag: str = "manual",
    priority: int = 100,
    requeue_terminal: bool = False,
    requeue_rejected: bool = False,
    requeue_completed: bool = False,
) -> dict[str, int]:
    """Lægger accessionsnumre i køen. Idempotent.

    Eksisterende poster røres ikke, medmindre det eksplicit er bedt om.
    De tre genindsætnings-flag dækker hver sin terminale tilstand og er
    bevidst adskilte, fordi de betyder noget forskelligt:

    * `requeue_terminal` nulstiller FAILED-poster — kilden fejlede,
      eller forsøgene blev brugt op. Selve dokumentet blev aldrig
      vurderet.
    * `requeue_rejected` nulstiller REJECTED-poster — dokumentet blev
      korrekt hentet, men den automatiske relevansscore var under
      lagringstærsklen. Det er en gyldig automatisk afgørelse, IKKE en
      fejl. At genbruge `requeue_terminal` til dette ville gøre det
      uklart, om man beder om at prøve en fejlet hentning igen, eller om
      at give et afvist dokument en ny chance.
    * `requeue_completed` nulstiller COMPLETED-poster — dokumentet blev
      hentet, vurderet og gemt uden problemer. Der er derfor normalt
      INGEN grund til at køre det igen, og flaget er det farligste af de
      tre: bruges det bredt, genimporteres hele samlingen. Det findes
      udelukkende, fordi en kurateret override, der registreres EFTER at
      dokumentet er importeret, ellers først ville slå igennem ved en
      tilfældig senere genimport — dokumentet ville blive stående med
      sin gamle, nu forkerte `is_maritime`-værdi.

      Dette lag håndhæver ikke selv den begrænsning; det kender ikke
      kurateringen. ``app.cli.cmd_backfill_enqueue`` tillader kun flaget
      sammen med en curated beslutning, og det er dér, politikken hører
      hjemme.

    Alle tre er begrænset til netop de `accession_numbers`, der angives
    her. Der findes intet "genindsæt alle"-flag — til det formål findes
    :func:`reset`, som er en særskilt, tydeligt navngiven operation.

    Returns:
        Antal ``added``, ``requeued`` og ``skipped``.
    """
    wanted = [
        str(a).strip() for a in accession_numbers if a is not None and str(a).strip()
    ]
    # Bevar rækkefølgen, men fjern dubletter i input.
    unique = list(dict.fromkeys(wanted))
    if not unique:
        return {"added": 0, "requeued": 0, "skipped": 0}

    existing = {
        item.accession_number: item
        for item in session.scalars(
            select(BackfillManifestItem).where(
                BackfillManifestItem.accession_number.in_(unique)
            )
        ).all()
    }

    added = requeued = skipped = 0
    for accn in unique:
        item = existing.get(accn)
        if item is None:
            session.add(
                BackfillManifestItem(
                    accession_number=accn,
                    source_tag=source_tag,
                    priority=priority,
                    status=BackfillStatus.PENDING.value,
                )
            )
            added += 1
        elif requeue_terminal and item.status == BackfillStatus.FAILED.value:
            _requeue_item(item)
            requeued += 1
        elif requeue_rejected and item.status == BackfillStatus.REJECTED.value:
            _requeue_item(item)
            requeued += 1
        elif requeue_completed and item.status == BackfillStatus.COMPLETED.value:
            _requeue_item(item)
            requeued += 1
        else:
            skipped += 1

    session.flush()
    logger.info(
        "backfill.enqueued",
        extra={
            "source_tag": source_tag,
            "added": added,
            "requeued": requeued,
            "skipped": skipped,
        },
    )
    return {"added": added, "requeued": requeued, "skipped": skipped}


def reset(session: Session, accession_numbers: Iterable[str] | None = None) -> int:
    """Sætter poster tilbage til PENDING, uanset nuværende status.

    Uden argument nulstilles hele køen. Bruges når taksonomien eller
    relevanstærsklen er ændret, og afviste dokumenter skal vurderes igen.
    """
    stmt = update(BackfillManifestItem).values(
        status=BackfillStatus.PENDING.value,
        claim_token=None,
        worker_id=None,
        attempt_count=0,
        last_error=None,
        next_attempt_at=None,
        processing_started_at=None,
        lease_expires_at=None,
        completed_at=None,
        updated_at=_now(),
    )
    if accession_numbers is not None:
        ids = [str(a) for a in accession_numbers]
        if not ids:
            return 0
        stmt = stmt.where(BackfillManifestItem.accession_number.in_(ids))

    result = session.execute(stmt)
    session.flush()
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------


def _claimable(now: datetime):
    """Betingelsen for at en post må tages."""
    return or_(
        BackfillManifestItem.status == BackfillStatus.PENDING.value,
        and_(
            BackfillManifestItem.status == BackfillStatus.RETRY.value,
            or_(
                BackfillManifestItem.next_attempt_at.is_(None),
                BackfillManifestItem.next_attempt_at <= now,
            ),
        ),
        # Forladt reservation: arbejderen er død eller hængt.
        and_(
            BackfillManifestItem.status == BackfillStatus.PROCESSING.value,
            BackfillManifestItem.lease_expires_at.is_not(None),
            BackfillManifestItem.lease_expires_at <= now,
        ),
    )


def claim_batch(
    session: Session,
    *,
    worker_id: str,
    batch_size: int = 25,
    lease_minutes: int = DEFAULT_LEASE_MINUTES,
) -> list[ClaimedItem]:
    """Reserverer op til `batch_size` poster.

    Reservationen committes før kalderen begynder at hente dokumenter, så
    en anden arbejder ikke tager de samme poster.

    Samtidighed håndteres i to lag:

    1. ``SELECT ... FOR UPDATE SKIP LOCKED`` på PostgreSQL, så to
       arbejdere ikke ser samme kandidat.
    2. En betinget ``UPDATE ... WHERE accession_number = :id AND
       status = :forventet_status``, der er atomisk på begge
       databasebackends. Rammer den nul rækker, kom en anden arbejder
       først, og kandidaten springes over. Derfor er koden korrekt også
       på SQLite, hvor ``SKIP LOCKED`` ikke findes.
    """
    if batch_size < 1:
        raise ValueError("batch_size skal være mindst 1.")
    if lease_minutes < 1:
        raise ValueError("lease_minutes skal være mindst 1.")

    now = _now()
    lease_expires_at = now + timedelta(minutes=lease_minutes)
    is_postgres = session.get_bind().dialect.name == "postgresql"
    # worker_id kommer fra værtsnavn og proces-id og kan være langt.
    stored_worker_id = worker_id[:ID_MAX_LENGTH]

    query = (
        select(BackfillManifestItem)
        .where(_claimable(now))
        .order_by(
            BackfillManifestItem.priority.asc(),
            BackfillManifestItem.next_attempt_at.asc().nulls_first(),
            BackfillManifestItem.accession_number.asc(),
        )
        # Hent lidt flere kandidater end nødvendigt: nogle kan blive taget
        # af en anden arbejder mellem SELECT og UPDATE.
        .limit(batch_size * 2)
    )
    if is_postgres:
        query = query.with_for_update(skip_locked=True)

    candidates = session.scalars(query).all()

    claimed: list[ClaimedItem] = []
    for candidate in candidates:
        if len(claimed) >= batch_size:
            break

        expected_status = candidate.status
        # Kun UUID'en. Arbejderens navn gemmes i worker_id, så det behøver
        # ikke også være en del af tokenet — og en sammensat streng kunne
        # overskride kolonnens 64 tegn ved et langt værtsnavn.
        token = uuid.uuid4().hex
        attempt = candidate.attempt_count + 1

        result = session.execute(
            update(BackfillManifestItem)
            .where(
                BackfillManifestItem.accession_number == candidate.accession_number,
                BackfillManifestItem.status == expected_status,
                # Den forladte reservation må ikke være blevet fornyet.
                BackfillManifestItem.claim_token.is_(candidate.claim_token)
                if candidate.claim_token is None
                else BackfillManifestItem.claim_token == candidate.claim_token,
            )
            .values(
                status=BackfillStatus.PROCESSING.value,
                claim_token=token,
                worker_id=stored_worker_id,
                attempt_count=attempt,
                processing_started_at=now,
                lease_expires_at=lease_expires_at,
                next_attempt_at=None,
                updated_at=now,
            )
        )

        if result.rowcount:
            claimed.append(
                ClaimedItem(
                    accession_number=candidate.accession_number,
                    token=token,
                    attempt=attempt,
                    lease_expires_at=lease_expires_at,
                )
            )
        else:
            logger.debug(
                "backfill.claim.lost_race",
                extra={"accession_number": candidate.accession_number},
            )

    # Reservationen skal være synlig for andre arbejdere, før hentningen
    # begynder. Derfor committes her og ikke af kalderen.
    session.commit()

    if claimed:
        logger.info(
            "backfill.claimed",
            extra={
                "worker_id": worker_id,
                "count": len(claimed),
                "lease_expires_at": lease_expires_at.isoformat(),
            },
        )
    return claimed


# ---------------------------------------------------------------------------
# Afslutning — altid bag fencing token
# ---------------------------------------------------------------------------


def _fenced_update(session: Session, accession_number: str, token: str, values: dict) -> bool:
    """Skriver kun hvis arbejderen stadig ejer reservationen."""
    result = session.execute(
        update(BackfillManifestItem)
        .where(
            BackfillManifestItem.accession_number == accession_number,
            BackfillManifestItem.claim_token == token,
            BackfillManifestItem.status == BackfillStatus.PROCESSING.value,
        )
        .values(**values, updated_at=_now())
    )
    if not result.rowcount:
        logger.warning(
            "backfill.fence.breach",
            extra={
                "accession_number": accession_number,
                "reason": "reservationen er udløbet eller overtaget; "
                "statusskrivningen droppes",
            },
        )
        return False
    return True


def finish(
    session: Session,
    item: ClaimedItem,
    status: BackfillStatus,
    *,
    error: str | None = None,
    import_run_id: int | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> BackfillStatus | None:
    """Afslutter en reserveret post.

    Gives `status` som RETRY, konverteres den til FAILED, hvis forsøgene
    er brugt op. Den faktisk skrevne status returneres, eller None hvis
    reservationen var tabt (fencing token matchede ikke).
    """
    if max_attempts < 1:
        raise ValueError("max_attempts skal være mindst 1.")

    now = _now()
    effective = status

    if status is BackfillStatus.RETRY and item.attempt >= max_attempts:
        effective = BackfillStatus.FAILED
        error = (
            f"{error or 'Ukendt fejl'} "
            f"(opgivet efter {item.attempt} forsøg)"
        )

    values: dict = {
        "status": effective.value,
        "last_error": (error or "")[:2000] or None,
        "claim_token": None,
        "lease_expires_at": None,
        "next_attempt_at": None,
        "completed_at": None,
        "import_run_id": import_run_id,
    }

    if effective is BackfillStatus.RETRY:
        # Posten skal tilbage i køen og må ikke ligne en igangværende
        # reservation, mens den venter.
        values.update(_RESERVATION_FIELDS)
        values["next_attempt_at"] = now + backoff_delay(item.attempt)
    elif effective in BackfillStatus.terminal():
        # `worker_id` og `processing_started_at` bevares her som revisionsspor:
        # sammen med completed_at viser de hvem der behandlede posten og hvor
        # længe det tog. Med claim_token = NULL og en endelig status kan rækken
        # ikke forveksles med en aktiv reservation.
        values["completed_at"] = now

    if not _fenced_update(session, item.accession_number, item.token, values):
        return None

    session.commit()
    logger.info(
        "backfill.item.finished",
        extra={
            "accession_number": item.accession_number,
            "status": effective.value,
            "attempt": item.attempt,
            "next_attempt_at": (
                values["next_attempt_at"].isoformat()
                if values["next_attempt_at"]
                else None
            ),
        },
    )
    return effective


def release(session: Session, item: ClaimedItem, *, reason: str) -> bool:
    """Giver en reserveret post tilbage til køen uden at bruge et forsøg.

    Bruges når kørslen blev afbrudt af årsager, der ikke har med posten
    at gøre — f.eks. at importeren stoppede efter for mange fejl i træk.
    """
    ok = _fenced_update(
        session,
        item.accession_number,
        item.token,
        {
            "status": BackfillStatus.PENDING.value,
            **_RESERVATION_FIELDS,
            # Forsøget tælles ikke mod max_attempts.
            "attempt_count": max(item.attempt - 1, 0),
            "last_error": reason[:2000],
        },
    )
    if ok:
        session.commit()
    return ok


# ---------------------------------------------------------------------------
# Indblik
# ---------------------------------------------------------------------------


def queue_counts(session: Session, *, source_tag: str | None = None) -> dict[str, int]:
    """Antal poster pr. status. Statusser uden poster medtages som 0."""
    stmt = select(
        BackfillManifestItem.status, func.count(BackfillManifestItem.accession_number)
    ).group_by(BackfillManifestItem.status)
    if source_tag is not None:
        stmt = stmt.where(BackfillManifestItem.source_tag == source_tag)

    counts = {status: 0 for status in BackfillStatus.values()}
    for status, count in session.execute(stmt).all():
        counts[str(status)] = int(count)
    counts["TOTAL"] = sum(v for k, v in counts.items() if k != "TOTAL")
    return counts


def pending_accessions(
    session: Session, limit: int = 20, *, source_tag: str | None = None
) -> Sequence[str]:
    """De næste accessionsnumre i køen. Kun til visning."""
    stmt = (
        select(BackfillManifestItem.accession_number)
        .where(_claimable(_now()))
        .order_by(
            BackfillManifestItem.priority.asc(),
            BackfillManifestItem.accession_number.asc(),
        )
        .limit(limit)
    )
    if source_tag is not None:
        stmt = stmt.where(BackfillManifestItem.source_tag == source_tag)
    return session.scalars(stmt).all()


def failed_items(
    session: Session, limit: int = 20, *, source_tag: str | None = None
) -> Sequence[BackfillManifestItem]:
    """Opgivne poster, nyeste først. Kun til visning."""
    stmt = (
        select(BackfillManifestItem)
        .where(BackfillManifestItem.status == BackfillStatus.FAILED.value)
        .order_by(BackfillManifestItem.updated_at.desc())
        .limit(limit)
    )
    if source_tag is not None:
        stmt = stmt.where(BackfillManifestItem.source_tag == source_tag)
    return session.scalars(stmt).all()

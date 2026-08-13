"""Tests for efterindlæsningskøen.

Dækker det, der faktisk kan gå galt i en kø delt af flere arbejdere:
reservationer, udløbne reservationer, fencing token, forsøgsgrænser og
oversættelsen fra importudfald til køstatus.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import BackfillManifestItem, BackfillStatus, Document
from app.services.backfill import manifest
from app.services.backfill.manifest import ClaimedItem
from app.services.backfill.worker import run_backfill
from app.services.retsinformation.base import (
    DocumentNotFoundError,
    DocumentRef,
    NormalizedDocument,
    TransientSourceError,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite gemmer DateTime(timezone=True) uden zoneoplysning.

    Værdien er UTC-vægtid, så den kan mærkes op igen ved læsning. Kun
    nødvendigt i tests: produktionskoden sammenligner tidsstempler i SQL
    eller mod værdier den selv har i hukommelsen.
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@pytest.fixture()
def queue_session(seeded_session):
    """Session med taksonomi indlæst og en tom kø."""
    return seeded_session


@contextmanager
def _scope(session):
    """session_factory til worker-tests: genbruger testsessionen."""
    yield session


# ---------------------------------------------------------------------------
# Opfyldning
# ---------------------------------------------------------------------------


def test_enqueue_is_idempotent(queue_session):
    first = manifest.enqueue(queue_session, ["A1", "A2", "A2"], source_tag="test")
    assert first == {"added": 2, "requeued": 0, "skipped": 0}

    second = manifest.enqueue(queue_session, ["A1", "A2", "A3"], source_tag="test")
    assert second == {"added": 1, "requeued": 0, "skipped": 2}

    total = queue_session.scalars(select(BackfillManifestItem)).all()
    assert {i.accession_number for i in total} == {"A1", "A2", "A3"}
    assert all(i.status == BackfillStatus.PENDING.value for i in total)


def test_enqueue_ignores_blank_input(queue_session):
    assert manifest.enqueue(queue_session, ["", "  ", None]) == {
        "added": 0,
        "requeued": 0,
        "skipped": 0,
    }


def test_requeue_failed_resets_attempts(queue_session):
    manifest.enqueue(queue_session, ["A1"])
    item = queue_session.get(BackfillManifestItem, "A1")
    item.status = BackfillStatus.FAILED.value
    item.attempt_count = 3
    item.last_error = "gammel fejl"
    queue_session.flush()

    counts = manifest.enqueue(queue_session, ["A1"], requeue_terminal=True)
    assert counts["requeued"] == 1

    queue_session.refresh(item)
    assert item.status == BackfillStatus.PENDING.value
    assert item.attempt_count == 0
    assert item.last_error is None


def test_completed_items_are_not_requeued_automatically(queue_session):
    manifest.enqueue(queue_session, ["A1"])
    item = queue_session.get(BackfillManifestItem, "A1")
    item.status = BackfillStatus.COMPLETED.value
    queue_session.flush()

    counts = manifest.enqueue(queue_session, ["A1"], requeue_terminal=True)
    assert counts == {"added": 0, "requeued": 0, "skipped": 1}
    queue_session.refresh(item)
    assert item.status == BackfillStatus.COMPLETED.value


def test_reset_returns_everything_to_pending(queue_session):
    manifest.enqueue(queue_session, ["A1", "A2"])
    for accn, status in (("A1", BackfillStatus.REJECTED), ("A2", BackfillStatus.FAILED)):
        item = queue_session.get(BackfillManifestItem, accn)
        item.status = status.value
        item.attempt_count = 2
    queue_session.flush()

    assert manifest.reset(queue_session) == 2
    items = queue_session.scalars(select(BackfillManifestItem)).all()
    assert all(i.status == BackfillStatus.PENDING.value for i in items)
    assert all(i.attempt_count == 0 for i in items)


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------


def test_claim_marks_items_processing_with_token(queue_session):
    manifest.enqueue(queue_session, ["A1", "A2"])

    claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=10)

    assert {c.accession_number for c in claimed} == {"A1", "A2"}
    assert len({c.token for c in claimed}) == 2  # unikt token pr. post

    for c in claimed:
        item = queue_session.get(BackfillManifestItem, c.accession_number)
        assert item.status == BackfillStatus.PROCESSING.value
        assert item.claim_token == c.token
        assert item.worker_id == "w1"
        assert item.attempt_count == 1
        assert _as_utc(item.lease_expires_at) > _now()


def test_claim_token_and_worker_id_fit_the_columns(queue_session):
    """PostgreSQL afviser for lange værdier; SQLite gør ikke."""
    manifest.enqueue(queue_session, ["A1"])
    long_worker = "vært" * 40  # langt over kolonnens 64 tegn

    claimed = manifest.claim_batch(queue_session, worker_id=long_worker, batch_size=1)[0]

    assert len(claimed.token) <= manifest.ID_MAX_LENGTH
    item = queue_session.get(BackfillManifestItem, "A1")
    assert len(item.claim_token) <= manifest.ID_MAX_LENGTH
    assert len(item.worker_id) <= manifest.ID_MAX_LENGTH


def test_default_worker_id_fits_the_column():
    from app.services.backfill.worker import default_worker_id

    assert len(default_worker_id()) <= manifest.ID_MAX_LENGTH


def test_claim_respects_batch_size_and_priority(queue_session):
    manifest.enqueue(queue_session, ["LOW"], priority=200)
    manifest.enqueue(queue_session, ["HIGH"], priority=1)

    claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)
    assert [c.accession_number for c in claimed] == ["HIGH"]


def test_claimed_item_is_not_handed_out_twice(queue_session):
    manifest.enqueue(queue_session, ["A1"])

    first = manifest.claim_batch(queue_session, worker_id="w1", batch_size=5)
    second = manifest.claim_batch(queue_session, worker_id="w2", batch_size=5)

    assert len(first) == 1
    assert second == []


def test_expired_lease_can_be_reclaimed(queue_session):
    manifest.enqueue(queue_session, ["A1"])
    stale = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)[0]

    item = queue_session.get(BackfillManifestItem, "A1")
    item.lease_expires_at = _now() - timedelta(minutes=1)
    queue_session.commit()

    fresh = manifest.claim_batch(queue_session, worker_id="w2", batch_size=1)
    assert len(fresh) == 1
    assert fresh[0].token != stale.token
    assert fresh[0].attempt == 2


def test_retry_item_is_not_claimed_before_its_time(queue_session):
    manifest.enqueue(queue_session, ["A1"])
    item = queue_session.get(BackfillManifestItem, "A1")
    item.status = BackfillStatus.RETRY.value
    item.next_attempt_at = _now() + timedelta(hours=1)
    queue_session.commit()

    assert manifest.claim_batch(queue_session, worker_id="w1", batch_size=5) == []

    item.next_attempt_at = _now() - timedelta(minutes=1)
    queue_session.commit()
    assert len(manifest.claim_batch(queue_session, worker_id="w1", batch_size=5)) == 1


def test_terminal_items_are_never_claimed(queue_session):
    manifest.enqueue(queue_session, ["DONE", "GONE", "NOPE"])
    for accn, status in (
        ("DONE", BackfillStatus.COMPLETED),
        ("GONE", BackfillStatus.FAILED),
        ("NOPE", BackfillStatus.REJECTED),
    ):
        queue_session.get(BackfillManifestItem, accn).status = status.value
    queue_session.commit()

    assert manifest.claim_batch(queue_session, worker_id="w1", batch_size=10) == []


def test_claim_batch_rejects_nonsense_batch_size(queue_session):
    with pytest.raises(ValueError):
        manifest.claim_batch(queue_session, worker_id="w1", batch_size=0)


def test_second_worker_in_its_own_session_sees_nothing(queue_session, database_url):
    """Reservationen skal være synlig for andre forbindelser, ikke kun
    for den session der lavede den."""
    from app.db.session import get_session_factory

    manifest.enqueue(queue_session, ["A1"])
    queue_session.commit()

    assert len(manifest.claim_batch(queue_session, worker_id="w1", batch_size=5)) == 1

    other = get_session_factory()()
    try:
        assert manifest.claim_batch(other, worker_id="w2", batch_size=5) == []
    finally:
        other.close()


def test_claim_is_skipped_when_row_changes_between_select_and_update(
    queue_session, database_url
):
    """Det betingede UPDATE er den portable garanti.

    ``SKIP LOCKED`` findes ikke på SQLite, så korrektheden må hvile på
    ``UPDATE ... WHERE status = :forventet``. Her stjæles posten af en
    anden forbindelse i netop det vindue, og reservationen skal da
    springes over frem for at blive uddelt to gange.
    """
    from app.db.session import get_session_factory

    manifest.enqueue(queue_session, ["A1"])
    queue_session.commit()

    thief = get_session_factory()()
    real_execute = queue_session.execute
    calls = {"n": 0}

    def racing_execute(statement, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Afslut læse-transaktionen, så den anden forbindelse kan skrive
            # (SQLite tillader ikke skrivning under en åben læsning).
            queue_session.rollback()
            manifest.claim_batch(thief, worker_id="tyv", batch_size=1)
        return real_execute(statement, *args, **kwargs)

    queue_session.execute = racing_execute  # type: ignore[method-assign]
    try:
        claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)
    finally:
        queue_session.execute = real_execute  # type: ignore[method-assign]
        thief.close()

    assert claimed == []
    item = queue_session.get(BackfillManifestItem, "A1")
    queue_session.refresh(item)
    assert item.worker_id == "tyv"
    assert item.attempt_count == 1  # ét forsøg brugt, ikke to


# ---------------------------------------------------------------------------
# Fencing token
# ---------------------------------------------------------------------------


def test_stale_worker_cannot_overwrite_new_owner(queue_session):
    """Kernen i fencing: en forsinket arbejder må ikke skrive."""
    manifest.enqueue(queue_session, ["A1"])
    stale = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)[0]

    # Reservationen udløber, og w2 overtager posten.
    queue_session.get(BackfillManifestItem, "A1").lease_expires_at = _now() - timedelta(
        minutes=1
    )
    queue_session.commit()
    fresh = manifest.claim_batch(queue_session, worker_id="w2", batch_size=1)[0]

    # w1 bliver færdig for sent og forsøger at skrive sit resultat.
    assert manifest.finish(queue_session, stale, BackfillStatus.FAILED) is None

    item = queue_session.get(BackfillManifestItem, "A1")
    queue_session.refresh(item)
    assert item.status == BackfillStatus.PROCESSING.value
    assert item.claim_token == fresh.token

    # w2's skrivning går igennem.
    assert manifest.finish(queue_session, fresh, BackfillStatus.COMPLETED) is (
        BackfillStatus.COMPLETED
    )
    queue_session.refresh(item)
    assert item.status == BackfillStatus.COMPLETED.value
    assert item.claim_token is None


def test_finish_on_unknown_token_is_a_no_op(queue_session):
    manifest.enqueue(queue_session, ["A1"])
    manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)

    bogus = ClaimedItem("A1", "opfundet-token", 1, _now() + timedelta(minutes=5))
    assert manifest.finish(queue_session, bogus, BackfillStatus.COMPLETED) is None
    assert (
        queue_session.get(BackfillManifestItem, "A1").status
        == BackfillStatus.PROCESSING.value
    )


def test_release_returns_item_without_spending_an_attempt(queue_session):
    manifest.enqueue(queue_session, ["A1"])
    claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)[0]
    assert claimed.attempt == 1

    assert manifest.release(queue_session, claimed, reason="kørsel afbrudt") is True

    item = queue_session.get(BackfillManifestItem, "A1")
    queue_session.refresh(item)
    assert item.status == BackfillStatus.PENDING.value
    assert item.attempt_count == 0
    assert item.claim_token is None


# ---------------------------------------------------------------------------
# Forsøg og ventetid
# ---------------------------------------------------------------------------


def test_retry_schedules_exponential_backoff(queue_session):
    manifest.enqueue(queue_session, ["A1"])
    claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)[0]

    written = manifest.finish(
        queue_session, claimed, BackfillStatus.RETRY, error="timeout", max_attempts=3
    )
    assert written is BackfillStatus.RETRY

    item = queue_session.get(BackfillManifestItem, "A1")
    queue_session.refresh(item)
    assert item.status == BackfillStatus.RETRY.value
    assert item.last_error == "timeout"
    delay = _as_utc(item.next_attempt_at) - _now()
    assert timedelta(minutes=4) < delay <= timedelta(minutes=5)


def test_backoff_grows_and_is_capped():
    assert manifest.backoff_delay(1) == timedelta(minutes=5)
    assert manifest.backoff_delay(2) == timedelta(minutes=15)
    assert manifest.backoff_delay(3) == timedelta(minutes=45)
    assert manifest.backoff_delay(20) == timedelta(hours=6)


def test_retry_becomes_failed_when_attempts_are_spent(queue_session):
    manifest.enqueue(queue_session, ["A1"])
    item = queue_session.get(BackfillManifestItem, "A1")
    item.attempt_count = 2  # tredje forsøg er det sidste
    queue_session.commit()

    claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)[0]
    assert claimed.attempt == 3

    written = manifest.finish(
        queue_session, claimed, BackfillStatus.RETRY, error="stadig timeout", max_attempts=3
    )
    assert written is BackfillStatus.FAILED

    queue_session.refresh(item)
    assert item.status == BackfillStatus.FAILED.value
    assert "opgivet efter 3 forsøg" in item.last_error
    assert item.next_attempt_at is None
    assert item.completed_at is not None


def test_retry_clears_the_reservation_fields(queue_session):
    """En ventende post må ikke ligne en igangværende reservation."""
    manifest.enqueue(queue_session, ["A1"])
    claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)[0]

    manifest.finish(queue_session, claimed, BackfillStatus.RETRY, error="timeout")

    item = queue_session.get(BackfillManifestItem, "A1")
    queue_session.refresh(item)
    assert item.claim_token is None
    assert item.worker_id is None
    assert item.lease_expires_at is None
    assert item.processing_started_at is None


def test_release_clears_the_reservation_fields(queue_session):
    manifest.enqueue(queue_session, ["A1"])
    claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)[0]

    manifest.release(queue_session, claimed, reason="afbrudt")

    item = queue_session.get(BackfillManifestItem, "A1")
    queue_session.refresh(item)
    assert item.claim_token is None
    assert item.worker_id is None
    assert item.lease_expires_at is None
    assert item.processing_started_at is None


def test_terminal_status_keeps_an_audit_trail(queue_session):
    """Færdige poster beholder hvem/hvornår, men ingen aktiv reservation."""
    manifest.enqueue(queue_session, ["A1"])
    claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)[0]

    manifest.finish(queue_session, claimed, BackfillStatus.COMPLETED)

    item = queue_session.get(BackfillManifestItem, "A1")
    queue_session.refresh(item)
    assert item.claim_token is None
    assert item.lease_expires_at is None
    assert item.completed_at is not None
    assert item.worker_id == "w1"
    assert item.processing_started_at is not None


def test_requeue_clears_the_previous_run(queue_session):
    """Genindsatte poster må ikke pege på den gamle kørsel."""
    from app.models import ImportRun

    old_run = ImportRun(source="test", client_kind="stub", trigger="test")
    queue_session.add(old_run)
    queue_session.flush()

    manifest.enqueue(queue_session, ["A1"])
    claimed = manifest.claim_batch(queue_session, worker_id="w1", batch_size=1)[0]
    manifest.finish(
        queue_session,
        claimed,
        BackfillStatus.FAILED,
        error="væk",
        import_run_id=old_run.id,
    )

    manifest.enqueue(queue_session, ["A1"], requeue_terminal=True)

    item = queue_session.get(BackfillManifestItem, "A1")
    queue_session.refresh(item)
    assert item.status == BackfillStatus.PENDING.value
    assert item.completed_at is None
    assert item.import_run_id is None
    assert item.worker_id is None
    assert item.processing_started_at is None
    assert item.last_error is None


def test_operational_arguments_are_validated(queue_session):
    for kwargs in (
        {"batch_size": 0},
        {"lease_minutes": 0},
        {"max_attempts": 0},
        {"max_batches": 0},
    ):
        with pytest.raises(ValueError):
            run_backfill(
                client=StubClient({}),
                worker_id="w1",
                session_factory=lambda: _scope(queue_session),
                **kwargs,
            )

    with pytest.raises(ValueError):
        manifest.claim_batch(queue_session, worker_id="w1", lease_minutes=0)


def test_status_listings_respect_the_tag_filter(queue_session):
    manifest.enqueue(queue_session, ["T1-A", "T1-B"], source_tag="t1")
    manifest.enqueue(queue_session, ["T2-A", "T2-B"], source_tag="t2")
    for accn in ("T1-B", "T2-B"):
        queue_session.get(BackfillManifestItem, accn).status = BackfillStatus.FAILED.value
    queue_session.commit()

    assert list(manifest.pending_accessions(queue_session, source_tag="t1")) == ["T1-A"]
    assert [i.accession_number for i in manifest.failed_items(queue_session, source_tag="t1")] == [
        "T1-B"
    ]
    # Uden filter ses begge manifests.
    assert len(manifest.pending_accessions(queue_session)) == 2
    assert len(manifest.failed_items(queue_session)) == 2


def test_queue_counts_reports_every_status(queue_session):
    manifest.enqueue(queue_session, ["A1", "A2"], source_tag="t1")
    manifest.enqueue(queue_session, ["B1"], source_tag="t2")
    queue_session.get(BackfillManifestItem, "A1").status = BackfillStatus.COMPLETED.value
    queue_session.commit()

    counts = manifest.queue_counts(queue_session)
    assert counts["TOTAL"] == 3
    assert counts["COMPLETED"] == 1
    assert counts["PENDING"] == 2
    assert counts["PROCESSING"] == 0

    scoped = manifest.queue_counts(queue_session, source_tag="t2")
    assert scoped["TOTAL"] == 1


# ---------------------------------------------------------------------------
# Arbejderen ende til ende
# ---------------------------------------------------------------------------


class StubClient:
    """Kildeklient med styrbare svar pr. accessionsnummer."""

    kind = "stub"

    def __init__(self, documents: dict[str, object]) -> None:
        self.documents = documents
        self.requested: list[str] = []

    def get_documents(self, *, since=None, explicit_ids=None):
        assert explicit_ids is not None, "arbejderen skal bruge explicit_ids"
        return [DocumentRef(source_id=str(a)) for a in explicit_ids]

    def get_updated_documents(self, since):  # pragma: no cover
        raise AssertionError("ikke brugt af efterindlæsningen")

    def get_document(self, document_id: str):
        self.requested.append(document_id)
        result = self.documents[document_id]
        if isinstance(result, Exception):
            raise result
        return result

    def get_document_metadata(self, document_id: str):  # pragma: no cover
        return self.get_document(document_id)

    def get_document_text(self, document_id: str) -> str:  # pragma: no cover
        return self.get_document(document_id).content

    def close(self) -> None:
        pass


def _maritime_doc(accn: str, content: str = "") -> NormalizedDocument:
    return NormalizedDocument(
        source="stub",
        source_id=accn,
        title="Bekendtgørelse om brandsikkerhed i passagerskibe",
        content=content
        or (
            "Reglerne gælder for passagerskibe og fastsætter krav til "
            "brandsikkerhed, redningsmidler og skibets sikkerhedsledelse "
            "efter SOLAS. Søfartsstyrelsen fører tilsyn."
        ),
        authority="Søfartsstyrelsen",
        document_type="Bekendtgørelse",
        status="Gældende",
    )


def _unrelated_doc(accn: str) -> NormalizedDocument:
    return NormalizedDocument(
        source="stub",
        source_id=accn,
        title="Bekendtgørelse om folkeskolens undervisning",
        content=(
            "Reglerne fastsætter rammerne for undervisningen i folkeskolen, "
            "elevernes timetal og kommunens tilsyn med skolerne."
        ),
        authority="Børne- og Undervisningsministeriet",
        document_type="Bekendtgørelse",
        status="Gældende",
    )


def test_worker_completes_maritime_and_rejects_unrelated(queue_session):
    manifest.enqueue(queue_session, ["MAR-1", "SKOLE-1"], source_tag="test")
    client = StubClient(
        {"MAR-1": _maritime_doc("MAR-1"), "SKOLE-1": _unrelated_doc("SKOLE-1")}
    )

    result = run_backfill(
        client=client,
        worker_id="w1",
        batch_size=10,
        session_factory=lambda: _scope(queue_session),
    )

    assert result.claimed == 2
    assert result.completed == 1
    assert result.rejected == 1
    assert result.failed == 0
    assert result.fence_breaches == 0
    assert len(result.import_run_ids) == 1

    assert (
        queue_session.get(BackfillManifestItem, "MAR-1").status
        == BackfillStatus.COMPLETED.value
    )
    rejected = queue_session.get(BackfillManifestItem, "SKOLE-1")
    assert rejected.status == BackfillStatus.REJECTED.value
    assert rejected.completed_at is not None

    stored = queue_session.scalars(select(Document)).all()
    assert [d.source_id for d in stored] == ["MAR-1"]
    # Kø-posten peger på den importkørsel der behandlede den.
    assert queue_session.get(
        BackfillManifestItem, "MAR-1"
    ).import_run_id == result.import_run_ids[0]


def test_worker_retries_transient_errors_and_gives_up_on_permanent(queue_session):
    manifest.enqueue(queue_session, ["FLAKY", "MISSING"])
    client = StubClient(
        {
            "FLAKY": TransientSourceError("HTTP 503 fra kilden"),
            "MISSING": DocumentNotFoundError("findes ikke hos kilden"),
        }
    )

    result = run_backfill(
        client=client,
        worker_id="w1",
        batch_size=10,
        max_batches=1,
        session_factory=lambda: _scope(queue_session),
    )

    assert result.retry == 1
    assert result.failed == 1

    flaky = queue_session.get(BackfillManifestItem, "FLAKY")
    assert flaky.status == BackfillStatus.RETRY.value
    assert flaky.next_attempt_at is not None
    assert "TransientSourceError" in flaky.last_error

    missing = queue_session.get(BackfillManifestItem, "MISSING")
    # Permanent fejl: ingen ventetid, ingen flere forsøg.
    assert missing.status == BackfillStatus.FAILED.value
    assert missing.next_attempt_at is None
    assert missing.attempt_count == 1


def test_worker_reprocessing_same_document_creates_no_extra_version(queue_session):
    """Køen må gerne behandle samme dokument igen — hashen fanger det."""
    from app.models import DocumentVersion

    client = StubClient({"MAR-1": _maritime_doc("MAR-1")})

    manifest.enqueue(queue_session, ["MAR-1"])
    run_backfill(
        client=client, worker_id="w1", batch_size=5,
        session_factory=lambda: _scope(queue_session),
    )

    manifest.reset(queue_session, ["MAR-1"])
    queue_session.commit()
    second = run_backfill(
        client=client, worker_id="w2", batch_size=5,
        session_factory=lambda: _scope(queue_session),
    )

    assert second.completed == 1
    versions = queue_session.scalars(select(DocumentVersion)).all()
    assert len(versions) == 1
    assert versions[0].version_number == 1


def test_worker_stops_when_queue_is_empty(queue_session):
    result = run_backfill(
        client=StubClient({}),
        worker_id="w1",
        session_factory=lambda: _scope(queue_session),
    )
    assert result.batches == 0
    assert result.claimed == 0


class ExplodingClient(StubClient):
    """Kilde hvor selv dokumentlisten ikke kan bygges."""

    def get_documents(self, *, since=None, explicit_ids=None):
        raise RuntimeError("kildelisten kunne ikke bygges")


def test_worker_stops_after_a_failed_import_instead_of_looping(queue_session):
    """Uden dette stopkriterium kører arbejderen i ring.

    `ImportService.run()` fanger discovery-fejlen og *returnerer* en
    FAILED-opsummering uden udfald. Frigives posterne til PENDING,
    reserveres de straks igen, og loopet slutter aldrig. Testen kører
    derfor bevidst UDEN `max_batches`.
    """
    manifest.enqueue(queue_session, ["A1", "A2"])

    result = run_backfill(
        client=ExplodingClient({}),
        worker_id="w1",
        batch_size=10,
        session_factory=lambda: _scope(queue_session),
    )

    assert result.batches == 1, "arbejderen tog mere end én portion"
    assert len(result.import_run_ids) == 1
    assert result.stopped_early is not None
    assert result.released == 0, "en FAILED-kørsel må ikke frigive uden forsøg"
    assert result.retry == 2

    for accn in ("A1", "A2"):
        item = queue_session.get(BackfillManifestItem, accn)
        assert item.status == BackfillStatus.RETRY.value
        assert item.attempt_count == 1
        assert item.next_attempt_at is not None
        assert item.claim_token is None


def test_repeated_failed_imports_eventually_give_up(queue_session):
    """Forsøgene skal løbe op, ikke nulstilles ved hver kørsel."""
    manifest.enqueue(queue_session, ["A1"])

    for expected_attempt in (1, 2, 3):
        run_backfill(
            client=ExplodingClient({}),
            worker_id="w1",
            batch_size=5,
            session_factory=lambda: _scope(queue_session),
        )
        item = queue_session.get(BackfillManifestItem, "A1")
        queue_session.refresh(item)
        assert item.attempt_count == expected_attempt
        # Gør posten klar med det samme, så næste kørsel tager den.
        item.next_attempt_at = _now() - timedelta(minutes=1)
        queue_session.commit()

    assert (
        queue_session.get(BackfillManifestItem, "A1").status
        == BackfillStatus.FAILED.value
    )


def test_worker_stops_when_import_service_raises(queue_session, monkeypatch):
    """Kaster `run()` i stedet for at returnere, gælder samme regel.

    Sker f.eks. hvis databasen falder væk, mens importkørslen oprettes.
    """
    manifest.enqueue(queue_session, ["A1"])

    def exploding_run(self, **kwargs):
        raise RuntimeError("databasen svarer ikke")

    monkeypatch.setattr(
        "app.services.backfill.worker.ImportService.run", exploding_run
    )

    result = run_backfill(
        client=StubClient({}),
        worker_id="w1",
        batch_size=5,
        session_factory=lambda: _scope(queue_session),
    )

    assert result.batches == 1
    assert result.stopped_early is not None
    assert (
        queue_session.get(BackfillManifestItem, "A1").status
        == BackfillStatus.RETRY.value
    )


def test_worker_honours_max_batches(queue_session):
    manifest.enqueue(queue_session, [f"MAR-{i}" for i in range(6)])
    client = StubClient({f"MAR-{i}": _maritime_doc(f"MAR-{i}") for i in range(6)})

    result = run_backfill(
        client=client,
        worker_id="w1",
        batch_size=2,
        max_batches=2,
        session_factory=lambda: _scope(queue_session),
    )

    assert result.batches == 2
    assert result.claimed == 4
    assert len(result.import_run_ids) == 2
    remaining = manifest.queue_counts(queue_session)
    assert remaining["PENDING"] == 2

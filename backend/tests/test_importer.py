"""Test af importservicen: idempotens, afvisning og fejlisolering."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import ChangeLogEntry, ChangeType, Document, ImportRun, ImportStatus
from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient
from app.services.retsinformation.base import DocumentNotFoundError, DocumentRef
from tests.conftest import FIXTURE_NON_MARITIME, FIXTURE_STORED, FIXTURE_TOTAL


def _service(session, client):
    return ImportService(
        session,
        client=client,
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    )


# ---------------------------------------------------------------------------
# Grundforløb
# ---------------------------------------------------------------------------


def test_import_opretter_kun_maritime_dokumenter(seeded_session):
    summary = _service(seeded_session, FixtureRetsinformationClient(revision=1)).run()

    assert summary.checked == FIXTURE_TOTAL
    assert summary.created == FIXTURE_STORED
    assert summary.rejected == FIXTURE_NON_MARITIME
    assert summary.failed == 0
    assert summary.status == ImportStatus.COMPLETED.value

    documents = seeded_session.scalars(select(Document)).all()
    assert len(documents) == FIXTURE_STORED
    assert all(d.maritime_score >= 30 for d in documents)


def test_genkoersel_giver_ingen_dubletter(seeded_session):
    """Importen skal kunne køres igen uden at skabe dubletter."""
    client = FixtureRetsinformationClient(revision=1)
    _service(seeded_session, client).run()
    summary = _service(seeded_session, client).run()

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.unchanged == FIXTURE_STORED
    assert len(seeded_session.scalars(select(Document)).all()) == FIXTURE_STORED


def test_revision_2_giver_forventede_aendringer(seeded_session):
    _service(seeded_session, FixtureRetsinformationClient(revision=1)).run()
    summary = _service(seeded_session, FixtureRetsinformationClient(revision=2)).run()

    assert summary.created == 1      # nyt dokument
    assert summary.updated == 2      # indholdsændring + statusændring
    assert summary.unchanged == FIXTURE_STORED - 2  # to dokumenter ændrede indhold

    typer = {
        e.change_type
        for e in seeded_session.scalars(select(ChangeLogEntry)).all()
    }
    assert ChangeType.CONTENT_UPDATED.value in typer
    assert ChangeType.STATUS_CHANGED.value in typer


def test_importkoersel_registreres(seeded_session):
    summary = _service(seeded_session, FixtureRetsinformationClient(revision=1)).run(
        trigger="test"
    )
    run = seeded_session.get(ImportRun, summary.import_run_id)

    assert run.status == ImportStatus.COMPLETED.value
    assert run.trigger == "test"
    assert run.client_kind == "fixture"
    assert run.finished_at is not None
    assert run.documents_checked == FIXTURE_TOTAL
    assert run.duration_seconds is not None


def test_limit_begraenser_antal_behandlede(seeded_session):
    summary = _service(seeded_session, FixtureRetsinformationClient(revision=1)).run(limit=5)
    assert summary.checked == 5


# ---------------------------------------------------------------------------
# Fejlisolering
# ---------------------------------------------------------------------------


class _FlakyClient:
    """Kilde hvor bestemte dokumenter altid fejler."""

    kind = "fixture"

    def __init__(self, failing_ids: set[str]):
        self._inner = FixtureRetsinformationClient(revision=1)
        self._failing = failing_ids

    def get_documents(self, *, since=None) -> list[DocumentRef]:
        return self._inner.get_documents(since=since)

    def get_updated_documents(self, since):
        return self._inner.get_updated_documents(since)

    def get_document(self, document_id: str):
        if document_id in self._failing:
            raise DocumentNotFoundError(f"Simuleret fejl for {document_id}")
        return self._inner.get_document(document_id)

    def get_document_metadata(self, document_id: str):
        return self._inner.get_document_metadata(document_id)

    def get_document_text(self, document_id: str) -> str:
        return self._inner.get_document_text(document_id)

    def close(self) -> None:
        self._inner.close()


def test_enkelte_fejlende_dokumenter_stopper_ikke_importen(seeded_session):
    """Ét dårligt dokument må ikke vælte hele kørslen."""
    fejlende = {"FIXT-BEK-2023-0101", "FIXT-BEK-2024-0088"}
    summary = _service(seeded_session, _FlakyClient(fejlende)).run()

    assert summary.failed == 2
    assert summary.created == FIXTURE_STORED - 2  # to dokumenter fejlede
    assert summary.status == ImportStatus.COMPLETED_WITH_ERRORS.value
    assert len(seeded_session.scalars(select(Document)).all()) == FIXTURE_STORED - 2

    fejlede_ids = {e["source_id"] for e in summary.errors}
    assert fejlede_ids == fejlende


def test_fejldetaljer_gemmes_paa_koerslen(seeded_session):
    summary = _service(seeded_session, _FlakyClient({"FIXT-BEK-2023-0101"})).run()
    run = seeded_session.get(ImportRun, summary.import_run_id)

    assert run.errors
    assert run.errors[0]["source_id"] == "FIXT-BEK-2023-0101"
    assert run.error_message


def test_mange_fejl_i_traek_afbryder_koerslen(seeded_session):
    """Fejler alt, er kilden nede — så skal importen stoppe."""
    alle = {r.source_id for r in FixtureRetsinformationClient(revision=1).get_documents()}
    service = ImportService(
        seeded_session,
        client=_FlakyClient(alle),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
        max_consecutive_failures=3,
    )
    summary = service.run()

    assert summary.status == ImportStatus.FAILED.value
    assert summary.checked == 3


class _BrokenDiscoveryClient:
    kind = "production"

    def get_documents(self, *, since=None):
        raise ConnectionError("Kilden svarer ikke")

    def get_updated_documents(self, since):
        raise ConnectionError("Kilden svarer ikke")

    def get_document(self, document_id):  # pragma: no cover
        raise AssertionError("bør ikke kaldes")

    def get_document_metadata(self, document_id):  # pragma: no cover
        raise AssertionError("bør ikke kaldes")

    def get_document_text(self, document_id):  # pragma: no cover
        raise AssertionError("bør ikke kaldes")

    def close(self):
        pass


def test_fejl_i_dokumentliste_markerer_koerslen_som_fejlet(seeded_session):
    summary = _service(seeded_session, _BrokenDiscoveryClient()).run()
    run = seeded_session.get(ImportRun, summary.import_run_id)

    assert summary.status == ImportStatus.FAILED.value
    assert run.status == ImportStatus.FAILED.value
    assert "Kunne ikke hente dokumentliste" in run.error_message


# ---------------------------------------------------------------------------
# Sporbarhed
# ---------------------------------------------------------------------------


def test_fixturdata_markeres_som_syntetisk(seeded_session):
    _service(seeded_session, FixtureRetsinformationClient(revision=1)).run()
    documents = seeded_session.scalars(select(Document)).all()

    assert all(d.is_synthetic is True for d in documents)
    assert all(d.source == "retsinformation-fixture" for d in documents)


def test_dokumenter_bevarer_kildesporbarhed(seeded_session):
    _service(seeded_session, FixtureRetsinformationClient(revision=1)).run()
    document = seeded_session.scalars(
        select(Document).where(Document.source_id == "FIXT-BEK-2023-0101")
    ).one()

    assert document.source_url
    assert document.retsinformation_id == "FIXT-BEK-2023-0101"
    assert document.last_retrieved_at is not None
    assert document.relevance_engine == "keyword"

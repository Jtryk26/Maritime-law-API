"""Planlagte job skal skelne fuldført databehandling fra delvis succes."""

from __future__ import annotations

import pytest
from app import cli
from app.models import ImportRun
from app.services.importer import DocumentRepository
from app.services.retsinformation import FixtureRetsinformationClient
from app.services.retsinformation.base import DocumentNotFoundError
from sqlalchemy import select
from tests.conftest import make_document


class ImportClient(FixtureRetsinformationClient):
    def __init__(self, outcome):
        super().__init__()
        self.outcome = outcome

    def get_documents(self, *, since=None):
        if self.outcome == "FAILED":
            raise ConnectionError("Simuleret utilgængelig kilde")
        return super().get_documents(since=since)

    def get_document(self, document_id):
        if self.outcome == "COMPLETED_WITH_ERRORS" and document_id == "FIXT-BEK-2023-0101":
            raise DocumentNotFoundError("Simuleret fejl i ét dokument")
        return super().get_document(document_id)


@pytest.mark.parametrize("status", ["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"])
@pytest.mark.parametrize("strict", [False, True])
def test_import_exit_matches_own_persisted_run(seeded_session, monkeypatch, status, strict):
    """En rigtig fixture-import med én kildefejl må ikke give grønt til timeren."""
    monkeypatch.setattr(cli, "build_source_client", lambda *a, **kw: ImportClient(status))
    args = cli.build_parser().parse_args(["import", *(["--require-complete"] if strict else [])])
    result = args.func(args)
    run = seeded_session.scalars(select(ImportRun).order_by(ImportRun.id.desc())).first()
    assert run.status == status
    expected_success = status == "COMPLETED" if strict else status != "FAILED"
    assert result == (0 if expected_success else 1)


def test_embed_completion_after_partial_and_full_indexing(
    seeded_session, relevance_engine, categorization_engine, monkeypatch,
):
    """Exit 0 fra embed run er ikke nok, hvis der stadig mangler dokumenter."""
    document = make_document("Bekendtgørelse om skibssikkerhed", "§ 1. Skibet skal være sødygtigt.")
    DocumentRepository(seeded_session).store(
        document, relevance_engine.classify(document), categorization_engine.categorize(document),
    )
    seeded_session.commit()
    parser = cli.build_parser()

    def run(*args):
        parsed = parser.parse_args(list(args))
        return parsed.func(parsed)

    assert run("embed", "status") == 0
    assert run("embed", "status", "--require-complete") == 1
    assert run("embed", "run", "--limit", "0") == 0
    assert run("embed", "status", "--require-complete") == 1
    assert run("embed", "run") == 0
    assert run("embed", "status", "--require-complete") == 0
    monkeypatch.setattr(cli, "vector_column_dimensions", lambda session: 999)
    assert run("embed", "status", "--require-complete") == 1

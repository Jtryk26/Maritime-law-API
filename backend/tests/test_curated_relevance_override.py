"""Test af kuraterede relevans-overrides.

Dækker den permanente, revisionssikre menneskelige rettelseskanal:
override-tabellen selv (oprettelse, opdatering, validering), hvordan
importservicen anvender den EFFEKTIVE afgørelse uden at røre den
automatiske motors egne tal, hvordan afgørelsen slår igennem i
efterindlæsningskøens status, og CLI'ens eksplicitte, afgrænsede
enqueue/curate-kommando.

Den centrale invariant, der testes overalt: `maritime_score` og
`relevance_details`s automatiske felter må ALDRIG ændre sig, uanset
hvilken override der findes. Kun `is_maritime` og køstatus er effektive.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    BackfillManifestItem,
    BackfillStatus,
    CuratedRelevanceOverride,
    CuratedRelevanceOverrideEvent,
    Document,
    DocumentVersion,
)
from app.services.backfill import manifest
from app.services.backfill.worker import run_backfill
from app.services.categorization import KeywordCategorizationEngine
from app.services.curation import (
    InvalidDecisionError,
    bulk_set_overrides,
    clear_override,
    get_override,
    list_overrides,
    override_history,
    set_override,
)
from app.services.importer import ImportService
from app.services.relevance.base import RelevanceResult
from app.services.retsinformation.base import DocumentRef, NormalizedDocument

# ---------------------------------------------------------------------------
# Hjælpere
# ---------------------------------------------------------------------------


class _ScriptedRelevanceEngine:
    """Relevansmotor med foruddefineret score pr. accessionsnummer.

    Bruges til at kunne påstå PRÆCISE automatiske scorer (35, 80, ...) i
    tests, uafhængigt af hvad den rigtige nøgleordsmotor ville finde på
    et givet stykke tekst.
    """

    name = "scripted-stub"

    def __init__(self, scores: dict[str, int]) -> None:
        self._scores = scores

    def classify(self, document: NormalizedDocument) -> RelevanceResult:
        score = self._scores.get(document.source_id, 0)
        if score >= 60:
            classification = "maritime"
        elif score >= 30:
            classification = "possible"
        else:
            classification = "not_maritime"
        return RelevanceResult(
            is_maritime=classification == "maritime",
            score=score,
            classification=classification,
            matched_terms=["stub-term"],
            reason="Scriptet testresultat",
            engine=self.name,
        )


class _SingleDocClient:
    """Kildeklient der altid returnerer ét bestemt (evt. udskifteligt) dokument."""

    kind = "stub"

    def __init__(self, doc: NormalizedDocument) -> None:
        self.doc = doc

    def get_documents(self, *, since=None, explicit_ids=None):
        ids = explicit_ids if explicit_ids is not None else [self.doc.source_id]
        return [DocumentRef(source_id=str(a)) for a in ids]

    def get_updated_documents(self, since):  # pragma: no cover
        raise AssertionError("ikke brugt i disse tests")

    def get_document(self, document_id: str) -> NormalizedDocument:
        assert document_id == self.doc.source_id
        return self.doc

    def get_document_metadata(self, document_id: str):  # pragma: no cover
        return self.get_document(document_id)

    def get_document_text(self, document_id: str) -> str:  # pragma: no cover
        return self.get_document(document_id).content

    def close(self) -> None:
        pass


def _doc(accn: str, content: str = "Indhold for testdokument.") -> NormalizedDocument:
    return NormalizedDocument(
        source="retsinformation",
        source_id=accn,
        title=f"Bekendtgørelse {accn}",
        content=content,
        authority="Testmyndigheden",
        document_type="Bekendtgørelse",
        status="Gældende",
    )


def _service(session, client, scores: dict[str, int]):
    return ImportService(
        session,
        client=client,
        relevance_engine=_ScriptedRelevanceEngine(scores),
        categorization_engine=KeywordCategorizationEngine(),
    )


# ---------------------------------------------------------------------------
# Selve override-tabellen
# ---------------------------------------------------------------------------


def test_set_override_opretter_og_opdaterer(seeded_session):
    created = set_override(
        seeded_session,
        "A1",
        "include",
        reason="Kontrolleret manuelt: maritim",
        source_tag="test-triage",
    )
    assert created.decision == "include"
    assert created.decision_source == "curated"
    assert created.reason == "Kontrolleret manuelt: maritim"
    assert created.source_tag == "test-triage"
    assert created.created_at is not None

    updated = set_override(
        seeded_session, "A1", "exclude", reason="Ombestemt", source_tag="test-triage-2"
    )
    assert updated.accession_number == "A1"
    assert updated.decision == "exclude"
    assert updated.reason == "Ombestemt"

    rows = seeded_session.scalars(select(CuratedRelevanceOverride)).all()
    assert len(rows) == 1  # opdatering, ikke en ny række


def test_set_override_afviser_ugyldig_decision(seeded_session):
    with pytest.raises(InvalidDecisionError):
        set_override(seeded_session, "A1", "maybe", reason="x", source_tag="t")


def test_set_override_afviser_tom_begrundelse(seeded_session):
    with pytest.raises(InvalidDecisionError):
        set_override(seeded_session, "A1", "include", reason="   ", source_tag="t")


def test_bulk_set_overrides_er_begraenset_til_de_angivne_numre(seeded_session):
    """Krav 7: genindsættelse/registrering må ALDRIG ramme andet end de
    eksplicit angivne accessionsnumre."""
    set_override(seeded_session, "ALLEREDE-EKSKLUDERET", "exclude", reason="x", source_tag="t0")

    counts = bulk_set_overrides(
        seeded_session,
        ["A1", "A2", "A2", "A3"],
        "include",
        reason="Global triage, godkendt",
        source_tag="global-triage-2026-08",
    )
    assert counts == {"created": 3, "updated": 0, "unchanged": 0}

    all_overrides = {o.accession_number: o for o in list_overrides(seeded_session)}
    assert set(all_overrides) == {"ALLEREDE-EKSKLUDERET", "A1", "A2", "A3"}
    # Den tidligere registrerede override for et andet nummer er urørt.
    assert all_overrides["ALLEREDE-EKSKLUDERET"].decision == "exclude"
    assert all_overrides["A1"].decision == "include"


def test_provenience_kan_aflaeses_igen(seeded_session):
    """Krav: 'Proveniens og begrundelse kan aflæses igen fra databasen.'"""
    set_override(
        seeded_session,
        "B20070023205",
        "include",
        reason="Menneskelig kontrol: reelt maritimt indhold trods lav fuldtekstscore",
        source_tag="curated-relevance-2026-08",
        decided_by="jacob",
    )

    row = get_override(seeded_session, "B20070023205")
    assert row is not None
    assert row.decision == "include"
    assert row.decision_source == "curated"
    assert row.reason.startswith("Menneskelig kontrol")
    assert row.source_tag == "curated-relevance-2026-08"
    assert row.decided_by == "jacob"
    assert row.created_at is not None
    assert row.updated_at is not None

    payload = row.to_json()
    assert payload["decision"] == "include"
    assert payload["reason"] == row.reason


# ---------------------------------------------------------------------------
# ImportService — den effektive afgørelse, automatikken uændret
# ---------------------------------------------------------------------------


def test_curated_include_med_lav_score_importeres_med_uaendret_score(seeded_session):
    """Krav: score 35 + curated include → importeret, score STADIG 35,
    is_maritime=True, COMPLETED (køstatus dækkes i worker-testen nedenfor)."""
    set_override(
        seeded_session, "LOW-1", "include", reason="Manuelt bekræftet maritimt", source_tag="t"
    )
    summary = _service(seeded_session, _SingleDocClient(_doc("LOW-1")), {"LOW-1": 35}).run(
        explicit_ids=["LOW-1"]
    )

    assert summary.created == 1
    assert summary.rejected == 0

    document = seeded_session.scalars(
        select(Document).where(Document.source_id == "LOW-1")
    ).one()
    assert document.is_maritime is True
    # Automatikkens egne tal er UÆNDREDE — ikke omskrevet til 60 eller mere.
    assert document.maritime_score == 35
    assert document.relevance_details["score"] == 35
    assert document.relevance_details["classification"] == "possible"
    assert document.relevance_details["curated_override"]["decision"] == "include"


def test_curated_exclude_med_hoej_score_undertrykkes(seeded_session):
    """Krav: score 80 + curated exclude → is_maritime=False, score STADIG 80."""
    set_override(
        seeded_session, "HIGH-1", "exclude", reason="Generel regulering, ikke maritim",
        source_tag="t",
    )
    summary = _service(seeded_session, _SingleDocClient(_doc("HIGH-1")), {"HIGH-1": 80}).run(
        explicit_ids=["HIGH-1"]
    )

    assert summary.rejected == 1
    assert summary.created == 0

    document = seeded_session.scalars(
        select(Document).where(Document.source_id == "HIGH-1")
    ).one()
    assert document.is_maritime is False
    assert document.maritime_score == 80
    assert document.relevance_details["score"] == 80
    assert document.relevance_details["classification"] == "maritime"
    assert document.relevance_details["curated_override"]["decision"] == "exclude"


def test_uden_override_er_adfaerden_uaendret(seeded_session):
    """Krav: 'ingen curated afgørelse → nuværende automatiske adfærd.'"""
    # Under lagringstærsklen (30), ingen override: skal IKKE gemmes, som i dag.
    summary_low = _service(
        seeded_session, _SingleDocClient(_doc("PLAIN-LOW")), {"PLAIN-LOW": 10}
    ).run(explicit_ids=["PLAIN-LOW"])
    assert summary_low.rejected == 1
    assert summary_low.created == 0
    assert (
        seeded_session.scalars(
            select(Document).where(Document.source_id == "PLAIN-LOW")
        ).first()
        is None
    )

    # Over tærsklen, ingen override: gemmes med den automatiske klassifikation.
    summary_high = _service(
        seeded_session, _SingleDocClient(_doc("PLAIN-HIGH")), {"PLAIN-HIGH": 80}
    ).run(explicit_ids=["PLAIN-HIGH"])
    assert summary_high.created == 1
    document = seeded_session.scalars(
        select(Document).where(Document.source_id == "PLAIN-HIGH")
    ).one()
    assert document.is_maritime is True
    assert document.maritime_score == 80
    assert "curated_override" not in document.relevance_details


def test_genkoersel_med_override_er_idempotent(seeded_session):
    """Krav: 'Genkørsel er idempotent og opretter ikke dubletter.'"""
    set_override(seeded_session, "LOW-1", "include", reason="x", source_tag="t")
    client = _SingleDocClient(_doc("LOW-1"))
    scores = {"LOW-1": 35}

    _service(seeded_session, client, scores).run(explicit_ids=["LOW-1"])
    second = _service(seeded_session, client, scores).run(explicit_ids=["LOW-1"])

    assert second.created == 0
    assert second.unchanged == 1
    document = seeded_session.scalars(
        select(Document).where(Document.source_id == "LOW-1")
    ).one()
    versions = seeded_session.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    ).all()
    assert len(versions) == 1


def test_curated_afgoerelse_bevares_ved_ny_dokumentversion(seeded_session):
    """Krav: 'En curated afgørelse skal fortsat gælde ved genimport og
    senere dokumentversioner.'"""
    set_override(seeded_session, "LOW-1", "include", reason="x", source_tag="t")
    client = _SingleDocClient(_doc("LOW-1", content="Første tekst."))
    scores = {"LOW-1": 35}

    _service(seeded_session, client, scores).run(explicit_ids=["LOW-1"])

    # Nyt indhold -> ny version. Automatisk score ændrer sig også (39), men
    # overriden gælder stadig.
    client.doc = _doc("LOW-1", content="Anden, ændret tekst.")
    scores["LOW-1"] = 39
    summary = _service(seeded_session, client, scores).run(explicit_ids=["LOW-1"])

    assert summary.updated == 1
    document = seeded_session.scalars(
        select(Document).where(Document.source_id == "LOW-1")
    ).one()
    assert document.is_maritime is True
    assert document.maritime_score == 39  # automatikkens NYE tal, uændret af override
    versions = seeded_session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number)
    ).all()
    assert [v.version_number for v in versions] == [1, 2]


def test_override_kan_registreres_foer_dokumentet_findes_lokalt(seeded_session):
    """De 16 kuraterede accessionsnumre findes typisk ikke lokalt endnu,
    når overriden registreres — det skal virke alligevel."""
    set_override(seeded_session, "IKKE-IMPORTERET-ENDNU", "include", reason="x", source_tag="t")
    assert (
        seeded_session.scalars(
            select(Document).where(Document.source_id == "IKKE-IMPORTERET-ENDNU")
        ).first()
        is None
    )
    # Override findes, selvom dokumentet ikke gør.
    assert get_override(seeded_session, "IKKE-IMPORTERET-ENDNU") is not None


# ---------------------------------------------------------------------------
# Efterindlæsningskøen — den effektive afgørelse slår igennem som COMPLETED/REJECTED
# ---------------------------------------------------------------------------


def _scoped_session(session):
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        yield session

    return _scope


def test_worker_curated_include_giver_completed(seeded_session, monkeypatch):
    """Krav 4: curated include → is_maritime=True og køstatus COMPLETED."""
    monkeypatch.setattr(
        "app.services.backfill.worker.get_relevance_engine",
        lambda: _ScriptedRelevanceEngine({"LOW-1": 35}),
    )
    set_override(seeded_session, "LOW-1", "include", reason="x", source_tag="t")
    manifest.enqueue(seeded_session, ["LOW-1"], source_tag="t")

    result = run_backfill(
        client=_SingleDocClient(_doc("LOW-1")),
        worker_id="w1",
        batch_size=10,
        session_factory=_scoped_session(seeded_session),
    )

    assert result.completed == 1
    assert result.rejected == 0
    item = seeded_session.get(BackfillManifestItem, "LOW-1")
    assert item.status == BackfillStatus.COMPLETED.value
    document = seeded_session.scalars(
        select(Document).where(Document.source_id == "LOW-1")
    ).one()
    assert document.is_maritime is True
    assert document.maritime_score == 35


def test_worker_curated_exclude_giver_rejected(seeded_session, monkeypatch):
    """Krav 4: curated exclude → is_maritime=False og køstatus REJECTED."""
    monkeypatch.setattr(
        "app.services.backfill.worker.get_relevance_engine",
        lambda: _ScriptedRelevanceEngine({"HIGH-1": 80}),
    )
    set_override(seeded_session, "HIGH-1", "exclude", reason="x", source_tag="t")
    manifest.enqueue(seeded_session, ["HIGH-1"], source_tag="t")

    result = run_backfill(
        client=_SingleDocClient(_doc("HIGH-1")),
        worker_id="w1",
        batch_size=10,
        session_factory=_scoped_session(seeded_session),
    )

    assert result.completed == 0
    assert result.rejected == 1
    item = seeded_session.get(BackfillManifestItem, "HIGH-1")
    assert item.status == BackfillStatus.REJECTED.value
    document = seeded_session.scalars(
        select(Document).where(Document.source_id == "HIGH-1")
    ).one()
    assert document.is_maritime is False
    assert document.maritime_score == 80


# ---------------------------------------------------------------------------
# CLI — eksplicit, afgrænset enqueue/curate
# ---------------------------------------------------------------------------


def test_cli_curated_include_registrerer_override_og_requeuer_kun_de_angivne(
    database_url, capsys
):
    """Krav 6+7: --curated-include/--curated-reason/--requeue-rejected, og
    KUN de eksplicit angivne accessionsnumre må genindsættes."""
    from app.cli import main
    from app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        manifest.enqueue(session, ["INCL-1", "ANDEN-AFVIST"], source_tag="orig")
        for accn in ("INCL-1", "ANDEN-AFVIST"):
            session.get(BackfillManifestItem, accn).status = BackfillStatus.REJECTED.value
        session.commit()
    finally:
        session.close()

    code = main(
        [
            "backfill", "enqueue",
            "--id", "INCL-1",
            "--tag", "curated-2026-08",
            "--curated-include",
            "--curated-reason", "Menneskelig kontrol: maritimt",
            "--requeue-rejected",
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "Curated override registreret" in output

    session = get_session_factory()()
    try:
        override = session.get(CuratedRelevanceOverride, "INCL-1")
        assert override is not None
        assert override.decision == "include"
        assert override.reason == "Menneskelig kontrol: maritimt"

        # Kun INCL-1 blev genindsat — ANDEN-AFVIST er urørt REJECTED.
        assert session.get(BackfillManifestItem, "INCL-1").status == BackfillStatus.PENDING.value
        assert (
            session.get(BackfillManifestItem, "ANDEN-AFVIST").status
            == BackfillStatus.REJECTED.value
        )
        # Der blev ikke registreret nogen override for det andet nummer.
        assert session.get(CuratedRelevanceOverride, "ANDEN-AFVIST") is None
    finally:
        session.close()


def test_cli_curated_reason_er_paakraevet(database_url, capsys):
    from app.cli import main

    code = main(
        ["backfill", "enqueue", "--id", "A1", "--curated-include"]
    )
    err = capsys.readouterr().err
    assert code == 2
    assert "curated-reason" in err


def test_cli_requeue_rejected_er_adskilt_fra_requeue_failed(database_url):
    """Krav: '--requeue-failed må ikke stiltiende genbruges til REJECTED'."""
    from app.cli import main
    from app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        manifest.enqueue(session, ["REJ-1", "FAIL-1"], source_tag="orig")
        session.get(BackfillManifestItem, "REJ-1").status = BackfillStatus.REJECTED.value
        session.get(BackfillManifestItem, "FAIL-1").status = BackfillStatus.FAILED.value
        session.commit()
    finally:
        session.close()

    # --requeue-failed alene rører IKKE REJECTED-posten.
    main(["backfill", "enqueue", "--id", "REJ-1", "--id", "FAIL-1", "--requeue-failed"])

    session = get_session_factory()()
    try:
        assert session.get(BackfillManifestItem, "REJ-1").status == BackfillStatus.REJECTED.value
        assert session.get(BackfillManifestItem, "FAIL-1").status == BackfillStatus.PENDING.value
    finally:
        session.close()

    # --requeue-rejected rører til gengæld REJECTED, ikke FAILED (som nu er PENDING).
    session = get_session_factory()()
    try:
        session.get(BackfillManifestItem, "FAIL-1").status = BackfillStatus.FAILED.value
        session.commit()
    finally:
        session.close()

    main(["backfill", "enqueue", "--id", "REJ-1", "--requeue-rejected"])

    session = get_session_factory()()
    try:
        assert session.get(BackfillManifestItem, "REJ-1").status == BackfillStatus.PENDING.value
        assert session.get(BackfillManifestItem, "FAIL-1").status == BackfillStatus.FAILED.value
    finally:
        session.close()


def test_cli_curated_status_viser_registrerede_overrides(database_url, capsys):
    from app.cli import main

    main(
        [
            "backfill", "enqueue", "--id", "A1", "--id", "A2",
            "--tag", "t", "--curated-include", "--curated-reason", "x",
        ]
    )
    capsys.readouterr()

    code = main(["backfill", "curated-status"])
    output = capsys.readouterr().out
    assert code == 0
    assert "A1" in output
    assert "A2" in output
    assert "include" in output


# ---------------------------------------------------------------------------
# Skemaoverensstemmelse — model vs. migration
# ---------------------------------------------------------------------------


class TestSkemaOverensstemmelse:
    """Værn mod dobbeltdefinerede indeks og navnedrift.

    Begge dele er stille fejl: metadata med to Index-objekter af samme
    navn fejler først ved `create_all` mod en tom base, og et
    constraint-navn, der afviger mellem model og migration, opdages først
    når nogen prøver at droppe eller ændre det i en senere migration.
    """

    def _table(self, name: str):
        from app.db.base import Base

        return Base.metadata.tables[name]

    def test_ingen_dublerede_indeks_i_nogen_tabel(self):
        """Hvert indeksnavn må kun forekomme én gang pr. tabel.

        `mapped_column(index=True)` og et eksplicit `Index(...)` på samme
        kolonne giver samme navn via NAMING_CONVENTION og dermed to
        objekter — præcis den fejl denne test blev skrevet efter.

        Testen dækker ALLE tabeller, ikke kun de kuraterede: da den blev
        skrevet, afslørede den samme dobbeltdefinition på
        `documents.document_number`, som havde ligget der siden 0001.
        """
        from app.db.base import Base

        offenders: dict[str, list[str]] = {}
        for table_name, table in Base.metadata.tables.items():
            names = [index.name for index in table.indexes]
            dupes = sorted({n for n in names if names.count(n) > 1})
            if dupes:
                offenders[table_name] = dupes

        assert not offenders, f"Dublerede indeksnavne i metadata: {offenders}"

    def test_de_to_forventede_indeks_findes_praecis_en_gang(self):
        table = self._table("curated_relevance_overrides")
        names = [index.name for index in table.indexes]
        assert names.count("ix_curated_relevance_overrides_decision") == 1
        assert names.count("ix_curated_relevance_overrides_source_tag") == 1
        assert len(names) == 2

    def test_create_all_mod_tom_base_lykkes(self, tmp_path):
        """Dublerede indeks ville give en CREATE INDEX-fejl her."""
        from sqlalchemy import create_engine, inspect

        from app.db.base import Base

        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
        try:
            Base.metadata.create_all(engine)

            inspector = inspect(engine)
            for table_name in (
                "curated_relevance_overrides",
                "curated_relevance_override_events",
            ):
                actual = [i["name"] for i in inspector.get_indexes(table_name)]
                assert len(actual) == len(set(actual)), (
                    f"{table_name} fik dublerede indeks i databasen: {actual}"
                )
        finally:
            engine.dispose()

    def test_model_og_migration_har_samme_navne(self, database_url):
        """Alembic-skemaet (migreret) skal matche modellernes metadata.

        `database_url`-fixturen har allerede kørt `alembic upgrade head`,
        så det, der inspiceres her, er præcis det skema, produktionen får.
        """
        from sqlalchemy import inspect

        from app.db.base import Base
        from app.db.session import get_session_factory

        session = get_session_factory()()
        try:
            inspector = inspect(session.get_bind())

            for table_name in (
                "curated_relevance_overrides",
                "curated_relevance_override_events",
            ):
                migrated_indexes = {i["name"] for i in inspector.get_indexes(table_name)}
                model_indexes = {i.name for i in Base.metadata.tables[table_name].indexes}
                assert migrated_indexes == model_indexes, (
                    f"{table_name}: indeksnavne afviger.\n"
                    f"  kun i migration: {migrated_indexes - model_indexes}\n"
                    f"  kun i model    : {model_indexes - migrated_indexes}"
                )

                migrated_checks = {
                    c["name"] for c in inspector.get_check_constraints(table_name)
                }
                model_checks = {
                    c.name
                    for c in Base.metadata.tables[table_name].constraints
                    if type(c).__name__ == "CheckConstraint"
                }
                assert migrated_checks == model_checks, (
                    f"{table_name}: check-constraint-navne afviger.\n"
                    f"  kun i migration: {migrated_checks - model_checks}\n"
                    f"  kun i model    : {model_checks - migrated_checks}"
                )
        finally:
            session.close()

    def test_migrationskaeden_naar_head_og_kan_rulles_tilbage(self, tmp_path, monkeypatch):
        """0003 skal kunne køres op OG ned mod en tom database.

        `migrations/env.py` henter bevidst forbindelsesstrengen fra
        applikationens settings og overskriver `sqlalchemy.url` fra
        alembic.ini. Derfor styres målbasen her via DATABASE_URL — sætter
        man kun main_option'en, migrerer testen den forkerte database.
        """
        from pathlib import Path

        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, inspect

        from app.core.config import get_settings

        url = f"sqlite:///{tmp_path / 'chain.db'}"
        monkeypatch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()

        backend_root = Path(__file__).resolve().parents[1]
        config = Config(str(backend_root / "alembic.ini"))
        config.set_main_option("script_location", str(backend_root / "migrations"))

        def _tables() -> set[str]:
            engine = create_engine(url)
            try:
                return set(inspect(engine).get_table_names())
            finally:
                engine.dispose()

        try:
            command.upgrade(config, "head")
            after_upgrade = _tables()
            assert "curated_relevance_overrides" in after_upgrade
            assert "curated_relevance_override_events" in after_upgrade
            # Hele kæden skal være kørt, ikke kun 0003.
            assert "documents" in after_upgrade
            assert "backfill_manifest_items" in after_upgrade

            command.downgrade(config, "0002_backfill_manifest")
            after_downgrade = _tables()
            assert "curated_relevance_overrides" not in after_downgrade
            assert "curated_relevance_override_events" not in after_downgrade
            # Rollback må ikke tage de foregående revisioners tabeller med.
            assert "documents" in after_downgrade
            assert "backfill_manifest_items" in after_downgrade
        finally:
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Append-only historik
# ---------------------------------------------------------------------------


class TestHistorik:
    def test_oprettelse_giver_created_haendelse(self, seeded_session):
        set_override(
            seeded_session, "H1", "include", reason="Første vurdering", source_tag="t1"
        )

        events = override_history(seeded_session, "H1")
        assert len(events) == 1
        assert events[0].event_type == "CREATED"
        assert events[0].previous_decision is None
        assert events[0].new_decision == "include"
        assert events[0].new_reason == "Første vurdering"
        assert events[0].new_source_tag == "t1"

    def test_skift_af_beslutning_bevarer_den_tidligere(self, seeded_session):
        """Kernekravet: en tidligere beslutning skal kunne aflæses EFTER
        at den er ændret."""
        set_override(
            seeded_session, "H1", "include", reason="Vurderet maritim", source_tag="t1"
        )
        set_override(
            seeded_session, "H1", "exclude", reason="Revurderet: ikke maritim",
            source_tag="t2",
        )

        # Den aktuelle afgørelse er den nye.
        assert get_override(seeded_session, "H1").decision == "exclude"

        events = override_history(seeded_session, "H1")
        assert [e.event_type for e in events] == ["CREATED", "DECISION_CHANGED"]

        change = events[1]
        assert change.previous_decision == "include"
        assert change.new_decision == "exclude"
        assert change.previous_reason == "Vurderet maritim"
        assert change.new_reason == "Revurderet: ikke maritim"
        assert change.previous_source_tag == "t1"
        assert change.new_source_tag == "t2"

    def test_aendret_begrundelse_giver_details_updated(self, seeded_session):
        set_override(seeded_session, "H1", "include", reason="Kort", source_tag="t1")
        set_override(
            seeded_session, "H1", "include", reason="Uddybet begrundelse", source_tag="t1"
        )

        events = override_history(seeded_session, "H1")
        assert [e.event_type for e in events] == ["CREATED", "DETAILS_UPDATED"]
        assert events[1].previous_reason == "Kort"
        assert events[1].new_reason == "Uddybet begrundelse"
        assert events[1].previous_decision == events[1].new_decision == "include"

    def test_aendret_source_tag_giver_details_updated(self, seeded_session):
        set_override(seeded_session, "H1", "include", reason="Samme", source_tag="t1")
        set_override(seeded_session, "H1", "include", reason="Samme", source_tag="t2")

        events = override_history(seeded_session, "H1")
        assert [e.event_type for e in events] == ["CREATED", "DETAILS_UPDATED"]
        assert events[1].previous_source_tag == "t1"
        assert events[1].new_source_tag == "t2"

    def test_uaendret_gentagelse_skriver_ingen_historikpost(self, seeded_session):
        """En idempotent gentagelse af samme kommando må ikke fylde
        historikken med indholdsløse rækker."""
        for _ in range(3):
            set_override(
                seeded_session, "H1", "include", reason="Samme", source_tag="t1"
            )

        events = override_history(seeded_session, "H1")
        assert len(events) == 1
        assert events[0].event_type == "CREATED"

    def test_clear_bevarer_den_fjernede_afgoerelse_i_historikken(self, seeded_session):
        """Kernekravet: efter clear skal det stadig kunne aflæses, at der
        var en override, hvad den sagde, og hvorfor."""
        set_override(
            seeded_session, "H1", "exclude", reason="Fejlregistreret", source_tag="t1"
        )
        assert clear_override(
            seeded_session, "H1", reason="Registreret ved en fejl", decided_by="jacob"
        ) is True

        # Den aktuelle override er væk.
        assert get_override(seeded_session, "H1") is None

        # Men historikken er intakt.
        events = override_history(seeded_session, "H1")
        assert [e.event_type for e in events] == ["CREATED", "CLEARED"]
        cleared = events[1]
        assert cleared.previous_decision == "exclude"
        assert cleared.new_decision is None
        assert cleared.previous_reason == "Fejlregistreret"
        assert cleared.new_reason == "Registreret ved en fejl"
        assert cleared.decided_by == "jacob"

    def test_clear_af_ukendt_nummer_skriver_ingen_historik(self, seeded_session):
        assert clear_override(seeded_session, "FINDES-IKKE") is False
        assert override_history(seeded_session, "FINDES-IKKE") == []

    def test_fuldt_forloeb_kan_rekonstrueres(self, seeded_session):
        """Oprettet -> skiftet -> uddybet -> fjernet, alt aflæseligt."""
        set_override(seeded_session, "H1", "include", reason="R1", source_tag="t1")
        set_override(seeded_session, "H1", "exclude", reason="R2", source_tag="t1")
        set_override(seeded_session, "H1", "exclude", reason="R3", source_tag="t1")
        clear_override(seeded_session, "H1", reason="R4")

        events = override_history(seeded_session, "H1")
        assert [e.event_type for e in events] == [
            "CREATED",
            "DECISION_CHANGED",
            "DETAILS_UPDATED",
            "CLEARED",
        ]
        # Hele beslutningsforløbet kan læses ud af rækkerne alene.
        assert [(e.previous_decision, e.new_decision) for e in events] == [
            (None, "include"),
            ("include", "exclude"),
            ("exclude", "exclude"),
            ("exclude", None),
        ]

    def test_bulk_set_skriver_historik_pr_nummer(self, seeded_session):
        bulk_set_overrides(
            seeded_session, ["H1", "H2"], "include", reason="R", source_tag="t"
        )
        assert len(override_history(seeded_session, "H1")) == 1
        assert len(override_history(seeded_session, "H2")) == 1
        assert len(override_history(seeded_session)) == 2

    def test_bulk_set_taeller_uaendrede_saerskilt(self, seeded_session):
        first = bulk_set_overrides(
            seeded_session, ["H1", "H2"], "include", reason="R", source_tag="t"
        )
        assert first == {"created": 2, "updated": 0, "unchanged": 0}

        second = bulk_set_overrides(
            seeded_session, ["H1", "H2", "H3"], "include", reason="R", source_tag="t"
        )
        assert second == {"created": 1, "updated": 0, "unchanged": 2}
        # De to uændrede har stadig kun deres CREATED-post.
        assert len(override_history(seeded_session, "H1")) == 1


# ---------------------------------------------------------------------------
# Genbehandling af et allerede COMPLETED dokument
# ---------------------------------------------------------------------------


def test_curated_exclude_paa_completed_dokument_ende_til_ende(
    seeded_session, monkeypatch
):
    """Hele forløbet fra det oprindelige problem:

    1. dokumentet importeres automatisk som maritimt, køen bliver COMPLETED
    2. en curated exclude registreres bagefter
    3. præcis dette dokument genindsættes og behandles
    4. dokumentet ender is_maritime=False
    5. køen ender REJECTED
    6. andre COMPLETED-poster forbliver urørte
    """
    scores = {"DOC-1": 80, "DOC-2": 85}
    monkeypatch.setattr(
        "app.services.backfill.worker.get_relevance_engine",
        lambda: _ScriptedRelevanceEngine(scores),
    )

    class _TwoDocClient:
        kind = "stub"

        def __init__(self):
            self.docs = {"DOC-1": _doc("DOC-1"), "DOC-2": _doc("DOC-2")}

        def get_documents(self, *, since=None, explicit_ids=None):
            return [DocumentRef(source_id=str(a)) for a in explicit_ids]

        def get_updated_documents(self, since):  # pragma: no cover
            raise AssertionError("ikke brugt")

        def get_document(self, document_id):
            return self.docs[document_id]

        def get_document_metadata(self, document_id):  # pragma: no cover
            return self.docs[document_id]

        def get_document_text(self, document_id):  # pragma: no cover
            return self.docs[document_id].content

        def close(self):
            pass

    client = _TwoDocClient()

    # 1. Automatisk import — begge er maritime og bliver COMPLETED.
    manifest.enqueue(seeded_session, ["DOC-1", "DOC-2"], source_tag="orig")
    run_backfill(
        client=client,
        worker_id="w1",
        batch_size=10,
        session_factory=_scoped_session(seeded_session),
    )

    assert seeded_session.get(BackfillManifestItem, "DOC-1").status == (
        BackfillStatus.COMPLETED.value
    )
    assert seeded_session.get(BackfillManifestItem, "DOC-2").status == (
        BackfillStatus.COMPLETED.value
    )
    doc1 = seeded_session.scalars(
        select(Document).where(Document.source_id == "DOC-1")
    ).one()
    assert doc1.is_maritime is True

    # 2. + 3. Curated exclude registreres, og netop dette nummer genindsættes.
    set_override(
        seeded_session, "DOC-1", "exclude",
        reason="Menneskelig kontrol: generel regulering, ikke maritim",
        source_tag="curated-2026-08",
    )
    counts = manifest.enqueue(
        seeded_session,
        ["DOC-1"],
        source_tag="curated-2026-08",
        requeue_completed=True,
    )
    assert counts["requeued"] == 1

    # 6. DOC-2 er urørt af genindsættelsen.
    assert seeded_session.get(BackfillManifestItem, "DOC-2").status == (
        BackfillStatus.COMPLETED.value
    )

    run_backfill(
        client=client,
        worker_id="w2",
        batch_size=10,
        session_factory=_scoped_session(seeded_session),
    )

    # 4. Dokumentet er nu effektivt ikke-maritimt...
    seeded_session.expire_all()
    doc1 = seeded_session.scalars(
        select(Document).where(Document.source_id == "DOC-1")
    ).one()
    assert doc1.is_maritime is False
    # ...men den automatiske score er stadig motorens egen.
    assert doc1.maritime_score == 80
    assert doc1.relevance_details["curated_override"]["decision"] == "exclude"

    # 5. Køen er REJECTED for netop dette nummer.
    assert seeded_session.get(BackfillManifestItem, "DOC-1").status == (
        BackfillStatus.REJECTED.value
    )

    # 6. DOC-2 er stadig COMPLETED og stadig maritimt.
    assert seeded_session.get(BackfillManifestItem, "DOC-2").status == (
        BackfillStatus.COMPLETED.value
    )
    doc2 = seeded_session.scalars(
        select(Document).where(Document.source_id == "DOC-2")
    ).one()
    assert doc2.is_maritime is True


def test_requeue_completed_uden_curated_beslutning_afvises(database_url, capsys):
    """Flaget må ikke kunne bruges til en bred genimport."""
    from app.cli import main
    from app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        manifest.enqueue(session, ["C1"], source_tag="orig")
        session.get(BackfillManifestItem, "C1").status = BackfillStatus.COMPLETED.value
        session.commit()
    finally:
        session.close()

    code = main(["backfill", "enqueue", "--id", "C1", "--requeue-completed"])
    err = capsys.readouterr().err

    assert code == 2
    assert "kun tilladt sammen med" in err

    session = get_session_factory()()
    try:
        assert session.get(BackfillManifestItem, "C1").status == (
            BackfillStatus.COMPLETED.value
        )
    finally:
        session.close()


def test_cli_requeue_completed_rammer_kun_de_angivne_numre(database_url, capsys):
    from app.cli import main
    from app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        manifest.enqueue(session, ["C1", "C2"], source_tag="orig")
        for accn in ("C1", "C2"):
            session.get(BackfillManifestItem, accn).status = BackfillStatus.COMPLETED.value
        session.commit()
    finally:
        session.close()

    code = main(
        [
            "backfill", "enqueue", "--id", "C1",
            "--tag", "curated-2026-08",
            "--curated-exclude", "--curated-reason", "Ikke maritim",
            "--requeue-completed",
        ]
    )
    assert code == 0

    session = get_session_factory()()
    try:
        assert session.get(BackfillManifestItem, "C1").status == (
            BackfillStatus.PENDING.value
        )
        assert session.get(BackfillManifestItem, "C2").status == (
            BackfillStatus.COMPLETED.value
        )
        assert session.get(CuratedRelevanceOverride, "C2") is None
    finally:
        session.close()


def test_cli_curated_history_viser_forloebet(database_url, capsys):
    from app.cli import main

    main(
        [
            "backfill", "enqueue", "--id", "A1", "--tag", "t1",
            "--curated-include", "--curated-reason", "Først maritim",
        ]
    )
    main(
        [
            "backfill", "enqueue", "--id", "A1", "--tag", "t2",
            "--curated-exclude", "--curated-reason", "Revurderet",
        ]
    )
    capsys.readouterr()

    code = main(["backfill", "curated-history", "--accession", "A1"])
    output = capsys.readouterr().out

    assert code == 0
    assert "CREATED" in output
    assert "DECISION_CHANGED" in output
    assert "include -> exclude" in output
    assert "Revurderet" in output

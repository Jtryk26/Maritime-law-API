"""Publiceringsværn for geografisk anvendelighed.

* Funktionen er slået fra som standard (GEOGRAPHY_ENABLED) og håndhæves i
  backend: ingen ruter, ingen analyse ved import.
* Opstarten skriver aldrig til de geografiske tabeller (driftsrollen har kun
  SELECT); seeding er `python -m app.cli geography seed`.
* Detektion er ikke godkendelse. Standardsvarene indeholder KUN godkendte,
  gældende tilknytninger; automatiske fund ligger under ``…/detections``.
* En menneskelig afgørelse overføres aldrig automatisk: enhver ændring af
  tekst, titel, status, version eller områdets definition kræver ny
  gennemgang. Den gamle afgørelse bevares som historik og vises som kontekst.
* Hav, MARPOL-særområde pr. bilag, SOx-ECA, NOx-ECA, PSSA og national
  jurisdiktion er forskellige områder, også når de deler navn.
* Ikke-efterprøvet hjemmel og datoer publiceres kun som foreløbige.
* Et kortmærke kaldes "Approximate area marker" og er aldrig en grænse.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, select

from app.models.geography import (
    Applicability,
    DocumentGeographicApplicability,
    GeographicArea,
    GeographicDetectionRun,
    GeoReviewStatus,
)
from app.services.categorization import KeywordCategorizationEngine
from app.services.geography import (
    PUBLICATION_LABELS,
    GeographyService,
    get_gazetteer,
    is_published,
)
from app.services.importer import ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient

from tests.test_geography import SULPHUR, _doc, _rows

DETECTED = "Detected geographic applicability — not reviewed"
REVIEWED = "Reviewed geographic applicability"


def _import(session, *records):
    ImportService(
        session,
        client=FixtureRetsinformationClient(documents=list(records)),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()
    session.commit()


def _changed(record, **changes):
    return {**record, **changes}


@pytest.fixture()
def seeded_areas(seeded_session):
    GeographyService(seeded_session).seed_areas()
    seeded_session.commit()
    return seeded_session


@pytest.fixture()
def imported(seeded_areas):
    _import(seeded_areas, SULPHUR)
    return seeded_areas


def _row(session, document, slug, **filters):
    rows = [r for s, r in _rows(session, document) if s == slug]
    for key, value in filters.items():
        rows = [r for r in rows if getattr(r, key) == value]
    assert rows, (slug, filters)
    return rows[0]


def _current(session, document, slug, **filters):
    return _row(session, document, slug, evidence_status="current", **filters)


def _review(session, row, status=GeoReviewStatus.CONFIRMED):
    GeographyService(session).review(row.id, status=status, reviewer="redaktør")
    session.commit()


def _reload(session):
    session.expire_all()
    return _doc(session, "GEO-BEK-0001")


def _area(session, slug):
    return session.scalars(select(GeographicArea).where(GeographicArea.slug == slug)).one()


def _published(session, document, row):
    return is_published(row, document, session.get(GeographicArea, row.area_id))


# ---------------------------------------------------------------------------
# 1. Opstart med en SELECT-rolle
# ---------------------------------------------------------------------------


def _block_writes(engine):
    def guard(conn, cursor, statement, params, context, executemany):
        verb = statement.lstrip().split(None, 1)[0].upper()
        if verb in {"INSERT", "UPDATE", "DELETE"} and "geographic" in statement:
            raise PermissionError(f"read-only role: {statement[:80]}")

    event.listen(engine, "before_cursor_execute", guard)
    return guard


def test_opstart_skriver_ikke_til_geografiske_tabeller(seeded_session):
    """Simulerer driftsrollen: skrivninger til geografi-tabellerne afvises."""
    from fastapi.testclient import TestClient

    from app.db.session import get_engine
    from app.main import app

    engine = get_engine()
    guard = _block_writes(engine)
    try:
        with TestClient(app) as client:
            # Tomme tabeller: opstarten lykkes, og svaret er blot tomt.
            assert client.get("/api/geographic-areas").json()["total"] == 0
            assert client.get("/api/geographic-areas/map").json()["features"] == []
            assert client.get("/api/search", params={"q": "skib"}).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", guard)
    assert seeded_session.scalars(select(GeographicArea)).first() is None


def test_seed_er_en_eksplicit_cli_handling(seeded_session, capsys):
    from app.cli import main

    assert seeded_session.scalars(select(GeographicArea)).first() is None
    assert main(["geography", "seed"]) == 0
    assert "Geografiske områder" in capsys.readouterr().out
    seeded_session.expire_all()
    assert seeded_session.scalars(select(GeographicArea)).first() is not None


# ---------------------------------------------------------------------------
# 2. Funktionsflag, håndhævet i backend
# ---------------------------------------------------------------------------


def test_flaget_er_slaaet_fra_som_standard(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("GEOGRAPHY_ENABLED", raising=False)
    assert Settings(_env_file=None).geography_enabled is False


@pytest.fixture()
def disabled_client(seeded_areas, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app
    from tests.conftest import TEST_ADMIN_TOKEN

    monkeypatch.setenv("GEOGRAPHY_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(
        create_app(get_settings()), headers={"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
    ) as client:
        yield client
    get_settings.cache_clear()


def test_slaaet_fra_ingen_geografiske_ruter(disabled_client):
    for path in (
        "/api/geographic-areas",
        "/api/geographic-areas/map",
        "/api/geographic-areas/kattegat",
        "/api/geographic-areas/kattegat/documents",
        "/api/geographic-areas/kattegat/detections",
        "/api/documents/1/geography",
        "/api/documents/1/geography/detections",
    ):
        assert disabled_client.get(path).status_code == 404, path
    assert disabled_client.post("/api/admin/geography/reanalyse/1").status_code in (404, 405)
    paths = disabled_client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "geograph" in p]


def test_slaaet_fra_soegning_og_dokumenter_virker(disabled_client, seeded_session):
    _import(seeded_session, SULPHUR)
    document = _doc(seeded_session, "GEO-BEK-0001")
    assert disabled_client.get("/api/search", params={"q": "svovl"}).status_code == 200
    assert disabled_client.get(f"/api/documents/{document.id}").status_code == 200
    assert disabled_client.get(f"/api/documents/{document.id}/structure").status_code == 200


def test_slaaet_fra_import_koerer_ikke_geografisk_analyse(seeded_session, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("GEOGRAPHY_ENABLED", "false")
    get_settings.cache_clear()
    try:
        _import(seeded_session, SULPHUR)
    finally:
        get_settings.cache_clear()
    assert seeded_session.scalars(select(DocumentGeographicApplicability)).first() is None
    assert seeded_session.scalars(select(GeographicDetectionRun)).first() is None


# ---------------------------------------------------------------------------
# 4. Det offentlige API: kun gennemgået som standard
# ---------------------------------------------------------------------------


def test_standardsvar_indeholder_kun_gennemgaaede(imported, api_client):
    default = api_client.get("/api/geographic-areas/north-sea/documents").json()
    assert default["result_kind"] == "reviewed_applicability"
    assert default["total"] == 0

    detections = api_client.get("/api/geographic-areas/north-sea/detections").json()
    assert detections["result_kind"] == "automatic_detection"
    assert "not reviewed" in detections["notice"]
    [item] = detections["items"]
    assert item["applicability"] == "APPLIES" and item["confidence"] >= 0.8
    assert item["result_kind"] == "automatic_detection"
    assert (item["publication_status"], item["publication_label"]) == ("detected_unreviewed", DETECTED)
    # Bevis og herkomst er med.
    assert item["match_text"] == "Nordsøen"
    assert item["source_excerpt"] == "Bekendtgørelsen gælder for skibe, der sejler i Østersøen og Nordsøen."
    assert item["source_excerpt"] in SULPHUR["content"]
    assert item["document_version_id"] and item["detection_method"] == "gazetteer"
    assert item["paragraph_anchor"] == "p-1-1"


def test_kun_menneskelig_godkendelse_publiceres(imported, api_client):
    document = _doc(imported, "GEO-BEK-0001")
    _review(imported, _row(imported, document, "north-sea", source_field="content"))

    [item] = api_client.get("/api/geographic-areas/north-sea/documents").json()["items"]
    assert item["result_kind"] == "reviewed_applicability"
    assert (item["publication_status"], item["publication_label"]) == ("reviewed", REVIEWED)
    assert api_client.get("/api/geographic-areas/north-sea/detections").json()["total"] == 0

    body = api_client.get(f"/api/documents/{document.id}/geography").json()
    assert [a["area"]["slug"] for a in body["areas"]] == ["north-sea"]
    assert body["areas"][0]["publication_label"] == REVIEWED
    assert body["areas"][0]["matches"][0]["reviewed_by"] == "redaktør"
    detections = api_client.get(f"/api/documents/{document.id}/geography/detections").json()
    assert "north-sea" not in {a["area"]["slug"] for a in detections["areas"]}
    assert all(a["result_kind"] == "automatic_detection" for a in detections["areas"])


def test_omtale_maerkes_som_omtale(imported, api_client):
    item = api_client.get(
        "/api/geographic-areas/sweden/detections", params={"applicability": "MENTIONS"}
    ).json()["items"][0]
    assert item["publication_label"] == "Detected geographic mention — not reviewed"


# ---------------------------------------------------------------------------
# 3. Ingen automatisk overførsel af godkendelser
# ---------------------------------------------------------------------------


def _assert_needs_rereview(session, document, slug, old, *, excerpt_unchanged, api_client=None):
    history = session.get(DocumentGeographicApplicability, old.id)
    assert history.evidence_status == "superseded"
    # Den oprindelige gennemgang er bevaret uændret som historik.
    assert history.review_status in ("confirmed", "rejected")
    assert history.reviewed_by == "redaktør" and history.reviewed_at is not None
    current = _current(session, document, slug, supersedes_id=old.id)
    assert current.review_status == "needs_rereview"
    assert current.reviewed_by is None and current.reviewed_at is None
    assert not _published(session, document, current)
    if api_client is not None:
        assert api_client.get(f"/api/geographic-areas/{slug}/documents").json()["total"] == 0
        [item] = [
            i for i in api_client.get(f"/api/geographic-areas/{slug}/detections").json()["items"]
            if i["document_id"] == document.id
        ]
        assert item["publication_status"] == "needs_rereview"
        assert item["publication_label"].startswith(DETECTED)
        assert item["previous_review"]["id"] == old.id
        assert item["previous_review"]["reviewed_by"] == "redaktør"
        assert item["previous_review"]["excerpt_unchanged"] is excerpt_unchanged
        assert "Context only" in item["previous_review"]["note"]
    return current


def test_aendret_lovtekst(imported, api_client):
    document = _doc(imported, "GEO-BEK-0001")
    old = _row(imported, document, "kattegat")
    _review(imported, old)
    _import(imported, _changed(SULPHUR, content=SULPHUR["content"].replace(
        "gælder ikke for skibe i Kattegat.", "gælder ikke for skibe under 20 meter i Kattegat.",
    )))
    document = _reload(imported)
    current = _assert_needs_rereview(
        imported, document, "kattegat", old, excerpt_unchanged=False, api_client=api_client
    )
    assert "under 20 meter" in current.context
    body = api_client.get(f"/api/documents/{document.id}/geography").json()
    assert body["areas"] == []
    assert [m["id"] for m in body["review_history"]] == [old.id]
    assert body["review_history"][0]["publication_status"] == "superseded"


def test_uaendret_uddrag_med_aendret_kontekst(imported, api_client):
    """§ 1 (Nordsøen) er ordret uændret, men en anden bestemmelse er ændret."""
    document = _doc(imported, "GEO-BEK-0001")
    old = _row(imported, document, "north-sea", source_field="content")
    _review(imported, old)
    _import(imported, _changed(SULPHUR, content=SULPHUR["content"] + (
        "§ 4. Bekendtgørelsen gælder ikke for skibe under 500 BT.\n"
    )))
    document = _reload(imported)
    current = _assert_needs_rereview(
        imported, document, "north-sea", old, excerpt_unchanged=True, api_client=api_client
    )
    assert current.evidence_hash == old.evidence_hash


def test_aendret_titel(imported, api_client):
    document = _doc(imported, "GEO-BEK-0001")
    version = document.current_version_id
    old = _row(imported, document, "north-sea", source_field="content")
    _review(imported, old)
    _import(imported, _changed(SULPHUR, title=SULPHUR["title"] + " (fritidsfartøjer)"))
    document = _reload(imported)
    _assert_needs_rereview(imported, document, "north-sea", old, excerpt_unchanged=True, api_client=api_client)
    # Beviset er uændret og versionen den samme — titlen alene ugyldiggør godkendelsen.
    assert document.current_version_id == version


def test_aendret_retlig_status(imported):
    document = _doc(imported, "GEO-BEK-0001")
    old = _row(imported, document, "north-sea", source_field="content")
    _review(imported, old)
    _import(imported, _changed(SULPHUR, status="Historisk"))
    document = _reload(imported)
    current = _assert_needs_rereview(imported, document, "north-sea", old, excerpt_unchanged=False)
    assert current.applicability == "HISTORICAL"


def test_aendret_version_uden_analyse_publiceres_ikke(imported, api_client, monkeypatch):
    """Ny version importeret, mens funktionen var slået fra: godkendelsen må ikke vises."""
    from app.core.config import get_settings

    document = _doc(imported, "GEO-BEK-0001")
    old = _row(imported, document, "north-sea", source_field="content")
    _review(imported, old)
    assert _published(imported, document, old)

    monkeypatch.setenv("GEOGRAPHY_ENABLED", "false")
    get_settings.cache_clear()
    try:
        _import(imported, _changed(SULPHUR, content=SULPHUR["content"] + "§ 5. Ny.\n"))
    finally:
        monkeypatch.setenv("GEOGRAPHY_ENABLED", "true")
        get_settings.cache_clear()
    document = _reload(imported)
    row = imported.get(DocumentGeographicApplicability, old.id)
    assert row.evidence_status == "current"  # Ikke analyseret igen ...
    assert not _published(imported, document, row)  # ... men ikke publiceret.
    assert api_client.get("/api/geographic-areas/north-sea/documents").json()["total"] == 0


def test_aendret_omraadedefinition_publiceres_ikke(imported, api_client):
    """Den støttende juridiske kontekst: områdets definition ændres."""
    document = _doc(imported, "GEO-BEK-0001")
    old = _row(imported, document, "north-sea", source_field="content")
    _review(imported, old)
    area = _area(imported, "north-sea")
    area.legal_basis = "Ændret hjemmel"
    imported.commit()
    assert not _published(imported, document, old)
    assert api_client.get("/api/geographic-areas/north-sea/documents").json()["total"] == 0

    GeographyService(imported).analyse(document, force=True)
    imported.commit()
    _assert_needs_rereview(imported, _reload(imported), "north-sea", old, excerpt_unchanged=True)


def test_tvungen_genkoersel_uden_aendringer_bevarer_godkendelsen(imported):
    document = _doc(imported, "GEO-BEK-0001")
    old = _row(imported, document, "north-sea", source_field="content")
    _review(imported, old)
    GeographyService(imported).analyse(document, force=True)
    imported.commit()
    row = imported.get(DocumentGeographicApplicability, old.id)
    assert (row.evidence_status, row.review_status) == ("current", "confirmed")
    assert _published(imported, _reload(imported), row)
    rows = [r for s, r in _rows(imported, document) if s == "north-sea" and r.source_field == "content"]
    assert len(rows) == 1


def test_tidligere_afvist_bliver_ikke_staaende_som_afgoerelse(imported):
    document = _doc(imported, "GEO-BEK-0001")
    old = _row(imported, document, "sweden")
    _review(imported, old, GeoReviewStatus.REJECTED)
    _import(imported, _changed(SULPHUR, title=SULPHUR["title"] + " m.v."))
    document = _reload(imported)
    history = imported.get(DocumentGeographicApplicability, old.id)
    assert (history.evidence_status, history.review_status) == ("superseded", "rejected")
    current = _current(imported, document, "sweden")
    assert current.review_status == "needs_rereview"  # Hverken stadig afvist eller godkendt.
    assert not _published(imported, document, current)


def test_manuel_tilknytning_kraever_ny_gennemgang(imported):
    document = _doc(imported, "GEO-BEK-0001")
    manual = GeographyService(imported).add_manual(
        document, _area(imported, "eca-baltic-sea-sox"), applicability=Applicability.APPLIES,
        reviewer="redaktør",
    )
    imported.commit()
    assert _published(imported, document, manual)

    GeographyService(imported).analyse(document, force=True)
    imported.commit()
    assert imported.get(DocumentGeographicApplicability, manual.id).evidence_status == "current"

    _import(imported, _changed(SULPHUR, content=SULPHUR["content"] + "§ 4. Ny bestemmelse.\n"))
    document = _reload(imported)
    assert imported.get(DocumentGeographicApplicability, manual.id).evidence_status == "superseded"
    copy = _current(imported, document, "eca-baltic-sea-sox")
    assert (copy.detection_method, copy.review_status, copy.supersedes_id) == (
        "manual", "needs_rereview", manual.id,
    )


def test_gentagne_aendringer_bevarer_needs_rereview_og_den_oprindelige_afgoerelse(imported, api_client):
    """En ny ændring, før nogen har gennemgået igen, må ikke nulstille markeringen."""
    document = _doc(imported, "GEO-BEK-0001")
    old = _row(imported, document, "kattegat")
    _review(imported, old)
    _import(imported, _changed(SULPHUR, title=SULPHUR["title"] + " m.v."))
    _import(imported, _changed(SULPHUR, title=SULPHUR["title"] + " m.v. (2)"))
    document = _reload(imported)
    current = _current(imported, document, "kattegat")
    assert (current.review_status, current.supersedes_id) == ("needs_rereview", old.id)
    [item] = api_client.get("/api/geographic-areas/kattegat/detections").json()["items"]
    assert item["previous_review"]["id"] == old.id
    assert item["previous_review"]["reviewed_by"] == "redaktør"


def test_gentagne_aendringer_af_manuel_tilknytning(imported):
    document = _doc(imported, "GEO-BEK-0001")
    manual = GeographyService(imported).add_manual(
        document, _area(imported, "eca-baltic-sea-sox"), applicability=Applicability.APPLIES,
        reviewer="redaktør",
    )
    imported.commit()
    _import(imported, _changed(SULPHUR, title=SULPHUR["title"] + " m.v."))
    _import(imported, _changed(SULPHUR, title=SULPHUR["title"] + " m.v. (2)"))
    document = _reload(imported)
    copy = _current(imported, document, "eca-baltic-sea-sox")
    assert (copy.review_status, copy.supersedes_id) == ("needs_rereview", manual.id)
    assert not _published(imported, document, copy)


def test_ny_gennemgang_af_det_aktuelle_fund(imported, api_client):
    document = _doc(imported, "GEO-BEK-0001")
    old = _row(imported, document, "kattegat")
    _review(imported, old)
    _import(imported, _changed(SULPHUR, title=SULPHUR["title"] + " m.v."))
    document = _reload(imported)
    current = _current(imported, document, "kattegat")
    with pytest.raises(LookupError):
        GeographyService(imported).review(old.id, status=GeoReviewStatus.CONFIRMED, reviewer="x")
    _review(imported, current)
    assert _published(imported, _reload(imported), current)
    assert api_client.get("/api/geographic-areas/kattegat/documents").json()["total"] == 1


# ---------------------------------------------------------------------------
# Forskellige geografier holdes adskilt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,slug",
    [
        ("i Østersøen", "baltic-sea"),
        ("in the Baltic Sea area", "marpol-baltic-sea-area"),
        ("within the Baltic Sea SECA", "eca-baltic-sea-sox"),
        ("in the Baltic Sea NOx Emission Control Area", "eca-baltic-sea-nox"),
        ("in the Baltic Sea ECA", "eca-baltic-sea"),
        ("in the Baltic Sea PSSA", "pssa-baltic-sea"),
        ("i dansk søterritorium", "danish-territorial-waters"),
        ("i danske farvande", "danish-waters"),
    ],
)
def test_hvert_navn_giver_netop_et_omraade(text, slug):
    assert [m.slug for m in get_gazetteer().find(text)] == [slug]


def test_bilagsspecifikke_saeromraader_tilknyttes_aldrig_automatisk():
    gazetteer = get_gazetteer()
    for annex in ("i", "iv", "v"):
        assert gazetteer.entries[f"marpol-annex-{annex}-baltic-sea-area"].aliases == []


def test_omraaderne_har_hver_sin_udpegning():
    entries = get_gazetteer().entries
    sox = entries["eca-baltic-sea-sox"].metadata["designation"]
    nox = entries["eca-baltic-sea-nox"].metadata["designation"]
    assert (sox["pollutant"], nox["pollutant"]) == ("SOx_PM", "NOx")
    assert entries["eca-baltic-sea"].metadata["designation"]["pollutant"] is None
    assert entries["marpol-baltic-sea-area"].metadata["designation"]["annex"] is None
    types = {entries[s].area_type for s in (
        "baltic-sea", "marpol-baltic-sea-area", "eca-baltic-sea-sox", "pssa-baltic-sea",
        "danish-territorial-waters",
    )}
    assert types == {"sea_area", "marpol_special_area", "eca", "pssa", "territorial_waters"}


def test_omtale_af_havet_knytter_ikke_til_dets_udpegninger(imported, api_client):
    document = _doc(imported, "GEO-BEK-0001")
    slugs = {slug for slug, _ in _rows(imported, document)}
    assert "baltic-sea" in slugs
    assert not {s for s in slugs if s.startswith(("eca-", "pssa-", "marpol-annex-"))}
    for slug in ("eca-baltic-sea-sox", "eca-baltic-sea-nox", "pssa-baltic-sea"):
        assert api_client.get(f"/api/geographic-areas/{slug}/detections").json()["total"] == 0
    items = api_client.get("/api/geographic-areas/baltic-sea/detections").json()["items"]
    assert {i["area_slug"] for i in items} == {"baltic-sea"}


# ---------------------------------------------------------------------------
# Ikke-efterprøvede metadata og ærlig geometri
# ---------------------------------------------------------------------------


def test_ikke_efterproevet_hjemmel_og_dato_er_kun_foreloebig(seeded_areas, api_client):
    detail = api_client.get("/api/geographic-areas/eca-baltic-sea-sox").json()
    assert detail["legal_status"] == "curated_unverified"
    assert detail["metadata_verified"] is False
    assert detail["legal_basis"] is None and detail["effective_from"] is None
    assert detail["provisional"]["legal_basis"] == "MARPOL Annex VI reg. 14.3"
    assert detail["provisional"]["effective_from"] == "2006-05-19"
    assert "unverified" in detail["provisional"]["status_note"].lower()
    listed = api_client.get("/api/geographic-areas", params={"area_type": "eca", "page_size": 100}).json()
    assert all(a["effective_from"] is None for a in listed["items"])


def test_efterproevet_omraade_viser_hjemmel(seeded_areas, api_client):
    area = _area(seeded_areas, "eca-baltic-sea-sox")
    area.legal_status = "verified"
    seeded_areas.commit()
    detail = api_client.get("/api/geographic-areas/eca-baltic-sea-sox").json()
    assert detail["legal_basis"] == "MARPOL Annex VI reg. 14.3"
    assert detail["effective_from"] == "2006-05-19"
    assert detail["provisional"] is None


def test_kortmaerke_kaldes_approximate_area_marker(seeded_areas, api_client):
    fc = api_client.get("/api/geographic-areas/map").json()
    point = next(f for f in fc["features"] if f["properties"]["slug"] == "kattegat")
    assert point["properties"]["geometry_label"] == "Approximate area marker"
    detail = api_client.get("/api/geographic-areas/kattegat").json()
    assert detail["geometry_label"] == "Approximate area marker"
    assert "boundary" not in detail["geometry_label"].lower()
    assert detail["display_extent"] is None


def test_ingen_rute_afgoer_om_en_position_ligger_i_et_omraade(api_client):
    paths = api_client.get("/openapi.json").json()["paths"]
    geo_paths = [p for p in paths if "geograph" in p]
    assert geo_paths
    for path in geo_paths:
        assert not any(word in path for word in ("contain", "inside", "point", "route", "position"))


def test_publiceringsetiketter_er_ordrette():
    assert PUBLICATION_LABELS["reviewed"] == REVIEWED
    assert PUBLICATION_LABELS["detected_unreviewed"] == DETECTED


# ---------------------------------------------------------------------------
# Migration 0014
# ---------------------------------------------------------------------------


def test_migration_0014_kan_rulles_tilbage(database_url):
    from alembic import command
    from sqlalchemy import create_engine, inspect

    from app.db.migrations_runner import alembic_config

    new = {"evidence_hash", "context_hash", "evidence_status", "supersedes_id"}

    def columns():
        engine = create_engine(database_url)
        try:
            return {c["name"] for c in inspect(engine).get_columns("document_geographic_applicability")}
        finally:
            engine.dispose()

    assert new <= columns()
    command.downgrade(alembic_config(), "0013_geographic_applicability")
    assert not (new & columns())
    command.upgrade(alembic_config(), "head")
    assert new <= columns()

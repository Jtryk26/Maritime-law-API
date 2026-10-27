"""Rettelser i den geografiske funktion efter gennemgangen af PR #34.

1. Nægtelser i dansk lovsprog bliver aldrig til APPLIES.
2. En gennemgang af et forældet fund afvises; historikken bevares.
3. Analysen opretter eller ændrer aldrig områder — kun ``geography seed``.
4. Rettighedspolitikken (`RightsService`) gælder for import, CLI og drift.
5. Kun en efterprøvet grænse med kilde og genbrugstilladelse er "official".

Alt kører på konstruerede fixturdokumenter. Ingen netværk, ingen model.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.geography import (
    Applicability,
    DocumentGeographicApplicability,
    GeographicArea,
    GeographicDetectionRun,
    GeographicReviewQueueItem,
    GeometryKind,
    GeoReviewStatus,
)
from app.services.categorization import KeywordCategorizationEngine
from app.services.geography import (
    GeographicAreasMissing,
    GeographyService,
    StaleEvidence,
    classify_match,
    effective_geometry_kind,
    official_geometry_problems,
    set_geometry,
)
from app.services.importer import ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient
from app.services.rights import Action

from tests.test_geography import SULPHUR, _doc, _record
from tests.test_geography_safeguards import (  # noqa: F401 - imported, seeded_areas er fixtures
    _area,
    _changed,
    _current,
    _import,
    _reload,
    _review,
    imported,
    seeded_areas,
)
from tests.test_source_rights import SECRET, grant, incoming, managed, store  # noqa: F401 - managed er en fixture

# ---------------------------------------------------------------------------
# 1. Nægtelser
# ---------------------------------------------------------------------------


def _verdict(text, name, **kwargs):
    start = text.index(name)
    return classify_match(text, start, start + len(name), **kwargs)


A, E, M, H = (Applicability.APPLIES, Applicability.EXEMPTS, Applicability.MENTIONS, Applicability.HISTORICAL)


@pytest.mark.parametrize(
    ("text", "name", "expected"),
    [
        # Nægtelser med mellemliggende ord, tegnsætning og ledsætninger
        ("Dette gælder dog ikke skibe i Nordsøen.", "Nordsøen", E),
        ("Kravet gælder heller ikke skibe i Nordsøen.", "Nordsøen", E),
        ("Kravene gælder ikke, når skibet sejler i Nordsøen.", "Nordsøen", E),
        ("Kravet finder dog ikke anvendelse i Nordsøen.", "Nordsøen", E),
        ("Undtaget herfra er skibe i Kattegat.", "Kattegat", E),
        ("Skibe i Kattegat er ikke omfattet af kravet.", "Kattegat", E),
        ("Ikke omfattet er skibe i Kattegat.", "Kattegat", E),
        ("Kattegat omfattes dog ikke af kravet.", "Kattegat", E),
        ("Bestemmelsen gælder imidlertid ikke for skibe, der sejler inden for Østersøen.", "Østersøen", E),
        ("Kravet anvendes ikke i Nordsøen.", "Nordsøen", E),
        ("Bestemmelsen gælder ikke for skibe i Kattegat.", "Kattegat", E),
        ("Loven finder ikke anvendelse på skibe i Kattegat.", "Kattegat", E),
        ("The regulation applies to ships, and does not apply in the North Sea.", "North Sea", E),
        ("This chapter shall not apply to ships operating in the Baltic Sea.", "Baltic Sea", E),
        ("Ships in the North Sea are exempt.", "North Sea", E),
        # Tvetydigt: konservativt MENTIONS, aldrig APPLIES
        ("Loven gælder ikke for fiskeskibe og gælder for skibe i Nordsøen.", "Nordsøen", M),
        ("Bekendtgørelsen gælder for skibe, der ikke sejler i Nordsøen.", "Nordsøen", M),
        ("Skibe skal ikke føre lys i Kattegat.", "Kattegat", M),
        ("Kravet gælder ikke for skibe, som er omfattet af reglerne i Kattegat.", "Kattegat", M),
        # Modsætning afslutter nægtelsen
        ("Loven gælder ikke for Færøerne, men gælder for skibe i Nordsøen.", "Nordsøen", A),
        # Bekræftende former er uændrede
        ("Bekendtgørelsen gælder for skibe i Kattegat.", "Kattegat", A),
        ("§ 3 gælder kun for skibe i Kattegat.", "Kattegat", A),
        ("Kravene gælder, når skibet sejler i Nordsøen.", "Nordsøen", A),
        ("Skibe skal føre lys i Kattegat.", "Kattegat", A),
        ("Skibe må ikke sejle i Kattegat.", "Kattegat", A),  # Forbud er anvendelse
        ("Ships shall not discharge oil while in the Baltic Sea.", "Baltic Sea", A),
        ("This regulation applies to ships in the North Sea and does not apply to warships.", "North Sea", A),
        ("It applies within the Baltic Sea, excluding the Kattegat.", "Kattegat", E),
        # Historik går forud for nægtelsen
        ("Reglerne gælder ikke længere for skibe i Kattegat.", "Kattegat", H),
        ("Kattegat er ikke længere omfattet af kravet.", "Kattegat", H),
    ],
)
def test_naegtelser_og_bekraeftelser(text, name, expected):
    assert _verdict(text, name).applicability == expected


@pytest.mark.parametrize(
    "text",
    [
        "Dette gælder dog ikke skibe i Nordsøen.",
        "Kravene gælder ikke, når skibet sejler i Nordsøen.",
        "Kravet finder dog ikke anvendelse i Nordsøen.",
        "Bekendtgørelsen gælder for skibe, der ikke sejler i Nordsøen.",
    ],
)
@pytest.mark.parametrize(("weight", "quality"), [(1.0, 1.0), (5.0, 1.0), (1.0, 0.2)])
def test_confidence_tilsidesaetter_aldrig_en_naegtelse(text, weight, quality):
    assert _verdict(text, "Nordsøen", weight=weight, quality=quality).applicability != A


def test_naegtelse_i_titel_bliver_ikke_applies():
    verdict = _verdict("Regler, der ikke gælder i Storebælt", "Storebælt", source_field="title")
    assert verdict.applicability != A


def test_importen_gemmer_naegtelsen_som_exempts(seeded_areas):
    _import(seeded_areas, _record("GEO-NEG-0001", "Bekendtgørelse om lys",
                                  "§ 1. Kravet gælder heller ikke skibe i Nordsøen.\n"))
    document = _doc(seeded_areas, "GEO-NEG-0001")
    row = _current(seeded_areas, document, "north-sea", source_field="content")
    assert row.applicability == "EXEMPTS"


# ---------------------------------------------------------------------------
# 2. Forældet bevis
# ---------------------------------------------------------------------------


def _without_analysis(monkeypatch, session, record):
    """Importerer, mens funktionen er slået fra: dokumentet ændres, fundene ikke."""
    monkeypatch.setenv("GEOGRAPHY_ENABLED", "false")
    get_settings.cache_clear()
    try:
        _import(session, record)
    finally:
        monkeypatch.setenv("GEOGRAPHY_ENABLED", "true")
        get_settings.cache_clear()


def test_gennemgang_af_aktuelt_bevis_accepteres(imported):
    document = _doc(imported, "GEO-BEK-0001")
    row = _current(imported, document, "north-sea", source_field="content")
    _review(imported, row)
    assert imported.get(DocumentGeographicApplicability, row.id).review_status == "confirmed"


@pytest.mark.parametrize(
    "change",
    [
        {"title": SULPHUR["title"] + " m.v."},
        {"status": "Historisk"},
        {"content": SULPHUR["content"] + "§ 9. Ny bestemmelse.\n"},
    ],
    ids=["titel", "status", "version"],
)
def test_gennemgang_af_forældet_bevis_afvises(imported, monkeypatch, change):
    document = _doc(imported, "GEO-BEK-0001")
    row = _current(imported, document, "north-sea", source_field="content")
    before = (row.review_status, row.reviewed_by, row.reviewed_at, row.evidence_status)
    _without_analysis(monkeypatch, imported, _changed(SULPHUR, **change))

    with pytest.raises(StaleEvidence, match="geography analyse --document-id"):
        GeographyService(imported).review(row.id, status=GeoReviewStatus.CONFIRMED, reviewer="redaktør")
    imported.rollback()
    imported.expire_all()
    row = imported.get(DocumentGeographicApplicability, row.id)
    assert (row.review_status, row.reviewed_by, row.reviewed_at, row.evidence_status) == before


def test_gennemgang_efter_aendret_omraadedefinition_afvises(imported):
    document = _doc(imported, "GEO-BEK-0001")
    row = _current(imported, document, "north-sea", source_field="content")
    _area(imported, "north-sea").legal_basis = "Ændret hjemmel"
    imported.commit()
    with pytest.raises(StaleEvidence):
        GeographyService(imported).review(row.id, status=GeoReviewStatus.REJECTED, reviewer="redaktør")


def test_tidligere_godkendelse_bevares_naar_forældet_bevis_afvises(imported, monkeypatch):
    document = _doc(imported, "GEO-BEK-0001")
    row = _current(imported, document, "north-sea", source_field="content")
    _review(imported, row)
    _without_analysis(monkeypatch, imported, _changed(SULPHUR, title=SULPHUR["title"] + " m.v."))

    with pytest.raises(StaleEvidence):
        GeographyService(imported).review(row.id, status=GeoReviewStatus.REJECTED, reviewer="en anden")
    imported.rollback()
    imported.expire_all()
    kept = imported.get(DocumentGeographicApplicability, row.id)
    assert (kept.review_status, kept.reviewed_by) == ("confirmed", "redaktør")

    # Efter en ny analyse er den gamle godkendelse historik, og det nye fund kan gennemgås.
    GeographyService(imported).analyse(_reload(imported), force=True)
    imported.commit()
    assert imported.get(DocumentGeographicApplicability, row.id).evidence_status == "superseded"
    new = _current(imported, _reload(imported), "north-sea", source_field="content")
    assert new.review_status == "needs_rereview" and new.supersedes_id == row.id
    _review(imported, new)
    assert imported.get(DocumentGeographicApplicability, new.id).review_status == "confirmed"


def test_cli_afviser_forældet_bevis(imported, monkeypatch, capsys):
    from app.cli import main

    document = _doc(imported, "GEO-BEK-0001")
    row = _current(imported, document, "north-sea", source_field="content")
    _without_analysis(monkeypatch, imported, _changed(SULPHUR, status="Historisk"))
    code = main(["geography", "review", str(row.id), "--status", "confirmed", "--reviewer", "redaktør"])
    assert code == 1
    assert "geography analyse" in capsys.readouterr().err
    imported.expire_all()
    assert imported.get(DocumentGeographicApplicability, row.id).review_status == "unreviewed"


# ---------------------------------------------------------------------------
# 3. Ingen implicit seeding
# ---------------------------------------------------------------------------


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_analyse_uden_omraader_fejler_uden_at_skrive(seeded_session, monkeypatch):
    _without_analysis(monkeypatch, seeded_session, SULPHUR)
    document = _doc(seeded_session, "GEO-BEK-0001")
    with pytest.raises(GeographicAreasMissing, match="python -m app.cli geography seed"):
        GeographyService(seeded_session).analyse(document)
    seeded_session.rollback()
    for model in (GeographicArea, GeographicDetectionRun, DocumentGeographicApplicability,
                  GeographicReviewQueueItem):
        assert _count(seeded_session, model) == 0


def test_manglende_enkelt_omraade_genskabes_ikke(seeded_areas):
    seeded_areas.delete(_area(seeded_areas, "kattegat"))
    seeded_areas.commit()
    _import(seeded_areas, SULPHUR)
    document = _doc(seeded_areas, "GEO-BEK-0001")
    with pytest.raises(GeographicAreasMissing, match="kattegat"):
        GeographyService(seeded_areas).analyse(document, force=True)
    seeded_areas.rollback()
    assert seeded_areas.scalars(select(GeographicArea).where(GeographicArea.slug == "kattegat")).first() is None


def test_import_uden_omraader_importerer_dokumentet(seeded_session):
    _import(seeded_session, SULPHUR)
    document = _doc(seeded_session, "GEO-BEK-0001")
    assert document.current_version_id is not None
    assert _count(seeded_session, GeographicArea) == 0
    assert _count(seeded_session, GeographicDetectionRun) == 0
    assert _count(seeded_session, DocumentGeographicApplicability) == 0


def test_cli_analyse_seeder_ikke(seeded_session, monkeypatch, capsys):
    from app.cli import main

    _without_analysis(monkeypatch, seeded_session, SULPHUR)
    assert main(["geography", "analyse"]) == 1
    assert "python -m app.cli geography seed" in capsys.readouterr().err
    seeded_session.expire_all()
    assert _count(seeded_session, GeographicArea) == 0


def test_drift_genanalyse_uden_omraader_giver_409(seeded_session, monkeypatch, api_client):
    _without_analysis(monkeypatch, seeded_session, SULPHUR)
    document = _doc(seeded_session, "GEO-BEK-0001")
    response = api_client.post(f"/api/admin/geography/reanalyse/{document.id}")
    assert response.status_code == 409
    assert "geography seed" in response.json()["detail"]
    seeded_session.expire_all()
    assert _count(seeded_session, GeographicArea) == 0


# ---------------------------------------------------------------------------
# 4. Rettigheder
# ---------------------------------------------------------------------------

GEO_TEXT = " § 2. Dette er opfundet testtekst: skibe i Østersøen skal føre lys. "
ALL_GEO_ACTIONS = (Action.FETCH_TEXT, Action.STORE_TEXT, Action.INDEX_TEXT, Action.SNIPPET)


def _with_geo_text(doc):
    return replace(doc, content=doc.content + GEO_TEXT)


def _managed_document(managed, actions):
    """Et rettighedsstyret dokument med de givne tilladelser og et stednavn i teksten."""
    session, _, _ = managed
    GeographyService(session).seed_areas()
    if actions:
        grant(session, actions=actions)
    outcome = store(session, _with_geo_text(incoming()))
    session.commit()
    return session, outcome.document


def _no_geography_rows(session):
    assert _count(session, DocumentGeographicApplicability) == 0
    assert _count(session, GeographicDetectionRun) == 0
    assert _count(session, GeographicReviewQueueItem) == 0


@pytest.mark.parametrize(
    "actions",
    [(), (Action.FETCH_TEXT, Action.STORE_TEXT), (Action.FETCH_TEXT, Action.STORE_TEXT, Action.INDEX_TEXT)],
    ids=["ingen-tilladelse", "kun-lagring", "uden-uddrag"],
)
def test_analyse_af_rettighedsstyret_dokument_afvises(managed, actions):
    session, document = _managed_document(managed, actions)
    result = GeographyService(session).analyse(document, force=True)
    session.commit()
    assert result.status == "RIGHTS_DENIED"
    _no_geography_rows(session)


def test_udtrykkelig_tilladelse_giver_analyse(managed):
    session, document = _managed_document(managed, ALL_GEO_ACTIONS)
    result = GeographyService(session).analyse(document, force=True)
    session.commit()
    assert result.status == "COMPLETED" and "baltic-sea" in result.by_area


def test_importkrogen_bruger_rettighedspolitikken(managed):
    session, document = _managed_document(managed, (Action.FETCH_TEXT, Action.STORE_TEXT))
    importer = ImportService(session, client=FixtureRetsinformationClient(documents=[]),
                             relevance_engine=KeywordRelevanceEngine(),
                             categorization_engine=KeywordCategorizationEngine())
    importer._sync_geography(document, _with_geo_text(incoming()))
    session.commit()
    _no_geography_rows(session)


def test_cli_analyse_springer_rettighedsstyrede_over(managed, capsys):
    from app.cli import main

    session, _ = _managed_document(managed, (Action.FETCH_TEXT, Action.STORE_TEXT))
    assert main(["geography", "analyse"]) == 0
    assert "RIGHTS_DENIED" in capsys.readouterr().out
    session.expire_all()
    _no_geography_rows(session)


def test_drift_genanalyse_af_rettighedsstyret_giver_403(managed, api_client):
    session, document = _managed_document(managed, (Action.FETCH_TEXT, Action.STORE_TEXT))
    response = api_client.post(f"/api/admin/geography/reanalyse/{document.id}")
    assert response.status_code == 403
    session.expire_all()
    _no_geography_rows(session)
    assert SECRET not in str(response.json())


def test_danske_dokumenter_analyseres_stadig(imported):
    document = _doc(imported, "GEO-BEK-0001")
    assert GeographyService(imported).analyse(document, force=True).status == "COMPLETED"


# ---------------------------------------------------------------------------
# 5. Officielle grænser
# ---------------------------------------------------------------------------

POLYGON = {"type": "Polygon", "coordinates": [[[10, 56], [12, 56], [12, 58], [10, 56]]]}
SOURCE = "https://example.invalid/official/boundary.geojson"


def _evidence(**overrides):
    evidence = {"source_url": SOURCE, "reviewer": "redaktør", "reviewed_at": "2026-01-15",
                "licence": "CC BY 4.0", "reuse_permitted": True}
    evidence.update(overrides)
    return evidence


def _verified(area, **overrides):
    area.legal_status = "verified"
    area.metadata_json = {**(area.metadata_json or {}), "boundary_evidence": _evidence(**overrides)}


@pytest.mark.parametrize(
    ("setup", "problem"),
    [
        (lambda a: None, "ikke efterprøvet"),
        (lambda a: setattr(a, "legal_status", "verified"), "boundary_evidence mangler"),
        (lambda a: _verified(a, reuse_permitted=False), "reuse_permitted"),
        (lambda a: _verified(a, licence=""), "licence"),
        (lambda a: _verified(a, reviewer=" "), "reviewer"),
        (lambda a: _verified(a, source_url="http://example.invalid/x"), "HTTPS"),
        (lambda a: _verified(a, reviewed_at=str(date.today() + timedelta(days=30))), "fremtiden"),
        (lambda a: _verified(a, reviewed_at="snart"), "dato"),
    ],
    ids=["uefterproevet", "ingen-bevis", "ingen-genbrug", "ingen-licens", "ingen-reviewer",
         "ikke-https", "fremtidig-dato", "ugyldig-dato"],
)
def test_officiel_grænse_kræver_efterproevet_grundlag(seeded_areas, setup, problem):
    area = _area(seeded_areas, "kattegat")
    setup(area)
    before = (area.geometry, area.geometry_kind, area.geometry_source)
    with pytest.raises(ValueError, match=problem):
        set_geometry(area, POLYGON, kind=GeometryKind.OFFICIAL, source=SOURCE)
    assert (area.geometry, area.geometry_kind, area.geometry_source) == before


def test_officiel_grænse_kræver_den_efterproevede_kilde(seeded_areas):
    area = _area(seeded_areas, "kattegat")
    _verified(area)
    with pytest.raises(ValueError, match="kilde"):
        set_geometry(area, POLYGON, kind=GeometryKind.OFFICIAL, source="https://example.invalid/other")


def test_officiel_grænse_med_fuldt_grundlag(seeded_areas, api_client):
    area = _area(seeded_areas, "kattegat")
    _verified(area)
    set_geometry(area, POLYGON, kind=GeometryKind.OFFICIAL, source=SOURCE)
    seeded_areas.commit()
    assert official_geometry_problems(area, SOURCE) == []
    body = api_client.get("/api/geographic-areas/kattegat").json()
    assert body["geometry_kind"] == "official"
    assert body["geometry_label"].startswith("Boundary copied from an official source")


def test_officiel_grænse_vises_som_omtrentlig_naar_grundlaget_mangler(seeded_areas, api_client):
    """Fx skrevet direkte i databasen eller efterprøvningen trukket tilbage i YAML."""
    area = _area(seeded_areas, "kattegat")
    area.geometry, area.geometry_kind, area.geometry_source = POLYGON, "official", SOURCE
    area.min_lon, area.min_lat, area.max_lon, area.max_lat = 10, 56, 12, 58
    seeded_areas.commit()
    assert effective_geometry_kind(area) == "approximate"
    body = api_client.get("/api/geographic-areas/kattegat").json()
    assert body["geometry_kind"] == "approximate"
    assert body["geometry_label"] == "Approximate outline — not a legal boundary"
    feature = next(f for f in api_client.get("/api/geographic-areas/map").json()["features"]
                   if f["properties"]["slug"] == "kattegat")
    assert feature["properties"]["geometry_kind"] == "approximate"


def test_cli_afviser_officiel_grænse_for_uefterproevet_omraade(seeded_areas, tmp_path, capsys):
    import json

    from app.cli import main

    path = tmp_path / "boundary.geojson"
    path.write_text(json.dumps(POLYGON), encoding="utf-8")
    code = main(["geography", "load-geometry", "kattegat", str(path), "--kind", "official", "--source", SOURCE])
    assert code == 1
    assert "ikke efterprøvet" in capsys.readouterr().err
    seeded_areas.expire_all()
    assert _area(seeded_areas, "kattegat").geometry_kind == "indicative_point"


def test_omtrentlig_geometri_kraever_ikke_efterproevning(seeded_areas):
    area = _area(seeded_areas, "kattegat")
    set_geometry(area, POLYGON, kind=GeometryKind.APPROXIMATE, source="Egen konstruktion")
    assert area.geometry_kind == "approximate"

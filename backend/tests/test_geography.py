"""Geografisk anvendelighed: genkendelse, klassifikation, lagring, migration og API.

Alt kører på konstruerede fixturdokumenter. Ingen netværk, ingen model.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select

from app.models import Document
from app.models.geography import (
    Applicability,
    DocumentGeographicApplicability,
    GeographicArea,
    GeographicDetectionRun,
    GeographicReviewQueueItem,
    GeoReviewStatus,
)
from app.services.categorization import KeywordCategorizationEngine
from app.services.geography import GeographyService, classify_match, geometry_bbox, get_gazetteer
from app.services.importer import ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient


def _record(source_id, title, content, status="Gældende"):
    return {
        "source_id": source_id,
        "retsinformation_id": source_id,
        "title": title,
        "short_title": None,
        "document_type": "Bekendtgørelse",
        "authority": "Søfartsstyrelsen",
        "ministry": "Erhvervsministeriet",
        "published_date": "2024-01-01",
        "effective_date": "2024-01-01",
        "status": status,
        "change_date": "2024-01-01",
        "document_number": source_id.rsplit("-", 1)[-1],
        "keywords": [],
        "source_url": f"https://example.invalid/fixture/{source_id}",
        "content": content,
    }


SULPHUR = _record(
    "GEO-BEK-0001",
    "Bekendtgørelse om svovlindhold i brændstof til skibe i Østersøen",
    "Kapitel 1\nAnvendelsesområde\n"
    "§ 1. Bekendtgørelsen gælder for skibe, der sejler i Østersøen og Nordsøen.\n"
    "Stk. 2. Bekendtgørelsen gælder ikke for skibe i Kattegat.\n"
    "§ 2. Ved Østersøområdet forstås det område, der er fastsat i MARPOL bilag I.\n"
    "§ 3. Skibe skal anvende brændstof med lavt svovlindhold. Reglerne i Isefjorden "
    "følger af aftalen med Sverige.\n",
)
PILOTAGE = _record(
    "GEO-BEK-0002",
    "Bekendtgørelse om lodspligt for skibe i Storebælt",
    "§ 1. Skibe skal benytte lods ved sejlads i Storebælt.\n",
)


@pytest.fixture()
def imported(seeded_session):
    ImportService(
        seeded_session,
        client=FixtureRetsinformationClient(documents=[SULPHUR, PILOTAGE]),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()
    seeded_session.commit()
    return seeded_session


def _doc(session, source_id) -> Document:
    return session.scalars(select(Document).where(Document.source_id == source_id)).one()


def _rows(session, document):
    rows = session.scalars(
        select(DocumentGeographicApplicability).where(
            DocumentGeographicApplicability.document_id == document.id
        )
    ).all()
    slugs = {a.id: a.slug for a in session.scalars(select(GeographicArea))}
    return [(slugs[r.area_id], r) for r in rows]


# ---------------------------------------------------------------------------
# Genkendelse
# ---------------------------------------------------------------------------


def _slugs(text):
    return [(m.slug, m.text) for m in get_gazetteer().find(text)]


def test_danske_boejninger_og_stavemaader_genkendes():
    assert _slugs("sejlads i Østersøen og Nordsøens farvand") == [
        ("baltic-sea", "Østersøen"), ("north-sea", "Nordsøens"),
    ]
    assert _slugs("i Oestersoeen") == [("baltic-sea", "Oestersoeen")]
    assert _slugs("in the BALTIC SEA") == [("baltic-sea", "BALTIC SEA")]
    assert _slugs("i svovlemissionskontrolområder") == [("eca-sox-unspecified", "svovlemissionskontrolområder")]


def test_laengste_fund_vinder():
    assert _slugs("ships in the Baltic Sea area") == [("marpol-baltic-sea-area", "Baltic Sea area")]
    assert _slugs("within the North Sea ECA") == [("eca-north-sea", "North Sea ECA")]


def test_ordgraense_kraeves():
    assert _slugs("Norgesbanken og Kattegatbroen") == []


def test_fuzzy_stavefejl_i_egennavn():
    [match] = get_gazetteer().find("sejlads i Skagerack")
    assert match.slug == "skagerrak"
    assert match.method == "gazetteer_fuzzy"
    assert match.quality < 1


def test_ukendt_stednavn_bliver_kandidat_ikke_omraade():
    from app.services.geography import find_unknown_places

    gazetteer = get_gazetteer()
    text = "Reglerne gælder i Gulf of Finland og i Østersøen."
    candidates = find_unknown_places(text, gazetteer, gazetteer.find(text))
    assert [c.text for c in candidates] == ["Gulf of Finland"]


# ---------------------------------------------------------------------------
# Klassifikation
# ---------------------------------------------------------------------------


def _classify(text, name, **kwargs):
    start = text.index(name)
    return classify_match(text, start, start + len(name), **kwargs)


@pytest.mark.parametrize(
    "text,name,expected",
    [
        ("Bekendtgørelsen gælder for skibe i Kattegat.", "Kattegat", Applicability.APPLIES),
        ("This regulation applies to ships operating in the Baltic Sea.", "Baltic Sea", Applicability.APPLIES),
        ("Skibe skal føre lys i Kattegat.", "Kattegat", Applicability.APPLIES),
        ("Stk. 2. Bekendtgørelsen gælder ikke for skibe i Kattegat.", "Kattegat", Applicability.EXEMPTS),
        ("It applies within the Baltic Sea, excluding the Kattegat.", "Kattegat", Applicability.EXEMPTS),
        ("It applies within the Baltic Sea, excluding the Kattegat.", "Baltic Sea", Applicability.APPLIES),
        ("In the North Sea this regulation shall not apply.", "North Sea", Applicability.EXEMPTS),
        ("Ved Østersøområdet forstås det område, der er fastsat.", "Østersøområdet", Applicability.DEFINES),
        ("The Baltic Sea area means the Baltic Sea proper.", "Baltic Sea area", Applicability.DEFINES),
        ("Reglerne for Kattegat er ophævet.", "Kattegat", Applicability.HISTORICAL),
        ("Kattegat er et farvand mellem Danmark og Sverige.", "Kattegat", Applicability.MENTIONS),
    ],
)
def test_klassifikation(text, name, expected):
    assert _classify(text, name).applicability == expected


def test_anvendelse_med_undtagelse_for_andre_skibe_er_ikke_undtagelse_for_omraadet():
    text = "This regulation applies to ships in the North Sea and does not apply to warships."
    assert _classify(text, "North Sea").applicability == Applicability.APPLIES


def test_stat_kan_hoejst_vaere_omtale():
    verdict = _classify("Loven gælder for skibe i Danmark.", "Danmark", mention_only=True, weight=0.5)
    assert verdict.applicability == Applicability.MENTIONS
    assert verdict.confidence <= 0.2


def test_historisk_dokument_giver_historical():
    verdict = _classify("Bekendtgørelsen gælder for skibe i Kattegat.", "Kattegat", document_status="Historisk")
    assert verdict.applicability == Applicability.HISTORICAL
    assert verdict.cue == "document_status"


def test_titel_med_stedsforholdsord():
    verdict = _classify("Bekendtgørelse om lodspligt i Storebælt", "Storebælt", source_field="title")
    assert verdict.applicability == Applicability.APPLIES


# ---------------------------------------------------------------------------
# Lagring gennem importen
# ---------------------------------------------------------------------------


def test_importen_gemmer_fund_med_anker_og_klassifikation(imported):
    rows = _rows(imported, _doc(imported, "GEO-BEK-0001"))
    found = {(slug, r.source_field, r.applicability) for slug, r in rows}
    assert ("baltic-sea", "title", "APPLIES") in found
    assert ("baltic-sea", "content", "APPLIES") in found
    assert ("north-sea", "content", "APPLIES") in found
    assert ("kattegat", "content", "EXEMPTS") in found
    assert ("marpol-baltic-sea-area", "content", "DEFINES") in found
    assert ("sweden", "content", "MENTIONS") in found

    kattegat = next(r for slug, r in rows if slug == "kattegat")
    assert kattegat.paragraph_anchor == "p-1-1-stk2"
    assert kattegat.match_text == "Kattegat"
    assert kattegat.review_status == "unreviewed"
    assert kattegat.detector_version and kattegat.document_version_id
    assert "gælder ikke" in kattegat.context


def test_ukendt_stednavn_gaar_i_koeen(imported):
    document = _doc(imported, "GEO-BEK-0001")
    queued = imported.scalars(
        select(GeographicReviewQueueItem).where(GeographicReviewQueueItem.document_id == document.id)
    ).all()
    assert [q.match_text for q in queued] == ["Isefjorden"]
    # Der oprettes aldrig et område for et ukendt navn.
    assert imported.scalars(select(GeographicArea).where(GeographicArea.slug.like("%isefjord%"))).first() is None


def test_uaendret_dokument_analyseres_ikke_igen(imported):
    document = _doc(imported, "GEO-BEK-0001")
    result = GeographyService(imported).analyse(document)
    assert result.status == "SKIPPED"
    runs = imported.scalars(
        select(GeographicDetectionRun).where(GeographicDetectionRun.document_id == document.id)
    ).all()
    assert len(runs) == 1


def test_gennemgaaede_fund_overlever_genanalyse(imported):
    document = _doc(imported, "GEO-BEK-0001")
    service = GeographyService(imported)
    kattegat = next(r for slug, r in _rows(imported, document) if slug == "kattegat")
    service.review(kattegat.id, status=GeoReviewStatus.CONFIRMED, reviewer="test")
    sweden = next(r for slug, r in _rows(imported, document) if slug == "sweden")
    service.review(sweden.id, status=GeoReviewStatus.REJECTED, reviewer="test")
    imported.commit()

    service.analyse(document, force=True)
    imported.commit()
    rows = _rows(imported, document)
    assert sum(1 for slug, _ in rows if slug == "kattegat") == 1
    assert next(r for slug, r in rows if slug == "kattegat").review_status == "confirmed"
    # Et afvist fund oprettes ikke igen som nyt, ikke-gennemgået fund.
    assert [r.review_status for slug, r in rows if slug == "sweden"] == ["rejected"]


def test_rettet_klassifikation_bliver_corrected(imported):
    document = _doc(imported, "GEO-BEK-0001")
    row = next(r for slug, r in _rows(imported, document) if slug == "north-sea")
    updated = GeographyService(imported).review(
        row.id, status=GeoReviewStatus.CONFIRMED, reviewer="test", applicability=Applicability.MENTIONS
    )
    assert (updated.applicability, updated.review_status) == ("MENTIONS", "corrected")


def test_bbox_afledes_af_geometrien():
    polygon = {"type": "Polygon", "coordinates": [[[10, 55], [12, 55], [12, 57], [10, 57], [10, 55]]]}
    assert geometry_bbox(polygon) == (10.0, 55.0, 12.0, 57.0)
    assert geometry_bbox({"type": "Point", "coordinates": [3.0, 56.0]}) == (3.0, 56.0, 3.0, 56.0)
    assert geometry_bbox(None) is None


def test_seed_overskriver_ikke_officiel_geometri(session):
    from app.models.geography import GeometryKind
    from app.services.geography import set_geometry

    service = GeographyService(session)
    service.seed_areas()
    area = session.scalars(select(GeographicArea).where(GeographicArea.slug == "kattegat")).one()
    polygon = {"type": "Polygon", "coordinates": [[[10, 56], [12, 56], [12, 58], [10, 56]]]}
    set_geometry(area, polygon, kind=GeometryKind.OFFICIAL, source="test")
    service.seed_areas()
    assert area.geometry_kind == "official" and area.geometry == polygon


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


GEO_TABLES = {
    "geographic_areas",
    "document_geographic_applicability",
    "geographic_detection_runs",
    "geographic_review_queue",
}


def test_migrationen_opretter_og_fjerner_tabellerne(database_url):
    from alembic import command
    from sqlalchemy import create_engine

    from app.db.migrations_runner import alembic_config

    def tables():
        engine = create_engine(database_url)
        try:
            return set(inspect(engine).get_table_names())
        finally:
            engine.dispose()

    assert GEO_TABLES <= tables()
    command.downgrade(alembic_config(), "0012_document_language_and_translation_rel")
    remaining = tables()
    assert not (GEO_TABLES & remaining)
    assert "documents" in remaining and "document_relations" in remaining
    command.upgrade(alembic_config(), "head")
    assert GEO_TABLES <= tables()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_api_omraader_og_kortdata(imported, api_client):
    page = api_client.get("/api/geographic-areas", params={"area_type": "eca"}).json()
    assert page["total"] >= 5
    assert {a["area_type"] for a in page["items"]} == {"eca"}

    box = api_client.get("/api/geographic-areas", params={"bbox": "10,55,13,58", "page_size": 100}).json()
    slugs = {a["slug"] for a in box["items"]}
    assert {"kattegat", "great-belt"} <= slugs
    assert "mediterranean-sea" not in slugs

    fc = api_client.get("/api/geographic-areas/map").json()
    assert fc["type"] == "FeatureCollection"
    baltic = next(f for f in fc["features"] if f["properties"]["slug"] == "baltic-sea")
    assert baltic["geometry"]["type"] == "Point"
    assert baltic["properties"]["geometry_kind"] == "indicative_point"
    # Ingen er gennemgået; fundet tælles kun som automatisk.
    assert baltic["properties"]["reviewed_document_count"] == 0
    assert baltic["properties"]["detected_document_count"] == 1
    assert "global" in {a["slug"] for a in fc["unmapped"]}
    assert "navigation" in fc["disclaimer"]

    assert api_client.get("/api/geographic-areas", params={"bbox": "nonsense"}).status_code == 422


def test_api_omraade_og_dets_fund(imported, api_client):
    detail = api_client.get("/api/geographic-areas/baltic-sea").json()
    assert "eca-baltic-sea-sox" in detail["related_areas"]
    assert detail["detected_counts_by_applicability"]["APPLIES"] == 1
    assert detail["reviewed_counts_by_applicability"] == {}

    assert api_client.get("/api/geographic-areas/atlantis").status_code == 404

    docs = api_client.get("/api/geographic-areas/kattegat/detections").json()
    assert docs["result_kind"] == "automatic_detection"
    [item] = docs["items"]
    assert item["result_kind"] == "automatic_detection"
    assert item["applicability"] == "EXEMPTS"
    assert item["document_path"].endswith("?p=p-1-1-stk2")
    assert item["review_status"] == "unreviewed"

    belt = api_client.get("/api/geographic-areas/great-belt/detections").json()
    assert [i["title"] for i in belt["items"]] == ["Bekendtgørelse om lodspligt for skibe i Storebælt"]

    # Omtaler vises kun, når der bedes om dem.
    assert api_client.get("/api/geographic-areas/sweden/detections").json()["total"] == 0
    mentions = api_client.get(
        "/api/geographic-areas/sweden/detections", params={"applicability": "MENTIONS"}
    ).json()
    assert mentions["total"] == 1


def test_api_dokumentets_fund(imported, api_client):
    document = _doc(imported, "GEO-BEK-0001")
    reviewed = api_client.get(f"/api/documents/{document.id}/geography").json()
    assert reviewed["areas"] == [] and reviewed["detected_area_count"] >= 4
    body = api_client.get(reviewed["detections_path"].replace("/documents", "/api/documents", 1)).json()
    assert body["result_kind"] == "automatic_detection"
    by_slug = {a["area"]["slug"]: a for a in body["areas"]}
    assert by_slug["kattegat"]["applicability"] == "EXEMPTS"
    assert by_slug["baltic-sea"]["applicability"] == "APPLIES"
    assert all(m["review_status"] == "unreviewed" for a in body["areas"] for m in a["matches"])
    assert api_client.get("/api/documents/999999/geography").status_code == 404
    assert api_client.get("/api/documents/999999/geography/detections").status_code == 404


def test_api_afviste_fund_vises_ikke(imported, api_client):
    document = _doc(imported, "GEO-BEK-0002")
    for _, r in _rows(imported, document):
        GeographyService(imported).review(r.id, status=GeoReviewStatus.REJECTED, reviewer="test")
    imported.commit()
    assert api_client.get("/api/geographic-areas/great-belt/detections").json()["total"] == 0
    assert api_client.get("/api/geographic-areas/great-belt/documents").json()["total"] == 0
    assert api_client.get(f"/api/documents/{document.id}/geography/detections").json()["areas"] == []


def test_api_genanalyse_kraever_administrator(imported, api_client, public_api_client):
    document = _doc(imported, "GEO-BEK-0002")
    url = f"/api/admin/geography/reanalyse/{document.id}"
    assert public_api_client.post(url).status_code == 401
    body = api_client.post(url).json()
    assert body["status"] == "COMPLETED"
    assert body["by_area"]["great-belt"] == "APPLIES"
    assert api_client.post("/api/admin/geography/reanalyse/999999").status_code == 404

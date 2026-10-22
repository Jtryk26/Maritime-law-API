"""Dokumentrelationer: historiske forgængere, ophævelser og hjemmel.

Unit-tests af udtrækket fra lovtekst og kildemetadata, og ende-til-ende
gennem import og API'et med en konstrueret lovkæde:

    LBK 500/2014  <-  LBK 700/2019  <-  LBK 900/2023      "lov om skibsudstyr"
                          ^
                          |  hjemmel i § 3, stk. 2 (jf. lovbekendtgørelse nr. 700)
                     BEK 1234/2023  --afløser-->  BEK 99/2010 (ikke i databasen)

Dokumenterne importeres i OMVENDT rækkefølge (nyeste først), så
henvisningerne må vente på deres mål og knyttes, når målet importeres.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Document, DocumentRelation
from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import ImportService
from app.services.relations import RelationRepository, RelationType, extract_relations
from app.services.relations.extract import extract_legal_basis, law_title_key
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "documents.json"


# ---------------------------------------------------------------------------
# Udtræk: hjemmel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "preamble,expected",
    [
        (
            "I medfør af § 24 b i lov om sikkerhed til søs fastsættes:",
            [("lov om sikkerhed til søs", "§ 24 b")],
        ),
        (
            "I medfør af § 5, stk. 3, og § 12 i lov om lodsning i grønlandske farvande "
            "fastsættes efter bemyndigelse:",
            [
                ("lov om lodsning i grønlandske farvande", "§ 5, stk. 3"),
                ("lov om lodsning i grønlandske farvande", "§ 12"),
            ],
        ),
        (
            "I medfør af §§ 3, 4 b og 7 i lov om skibsudstyr fastsættes:",
            [("lov om skibsudstyr", "§ 3"), ("lov om skibsudstyr", "§ 4 b"),
             ("lov om skibsudstyr", "§ 7")],
        ),
        (
            "I medfør af § 1, stk. 2, i lov om sikkerhed til søs, jf. lovbekendtgørelse "
            "nr. 1629 af 20. december 2016, fastsættes:",
            [("LBK 1629/2016", "§ 1, stk. 2")],
        ),
        (
            "I medfør af § 3 i lov om sikkerhed til søs, jf. lovbekendtgørelse nr. 1629 af "
            "20. december 2016, og § 5, nr. 2, i lov om beskyttelse af havmiljøet, jf. "
            "lovbekendtgørelse nr. 1165 af 25. november 2019, fastsættes:",
            [("LBK 1629/2016", "§ 3"), ("LBK 1165/2019", "§ 5, nr. 2")],
        ),
        (
            "I medfør af § 7 i lov nr. 456 af 10. juni 2020 om havne fastsættes:",
            [("LOV 456/2020", "§ 7")],
        ),
    ],
)
def test_hjemmel_udtraekkes_pr_paragraf(preamble, expected):
    relations = extract_legal_basis(preamble)
    assert [(r.target_reference, r.specific_section) for r in relations] == expected
    assert all(r.relation_type == RelationType.EMPOWERED_BY for r in relations)


def test_hjemmel_bevarer_lovens_titel_til_visning():
    [relation] = extract_legal_basis(
        "I medfør af § 1, stk. 2, i lov om sikkerhed til søs, jf. lovbekendtgørelse "
        "nr. 1629 af 20. december 2016, fastsættes:"
    )
    assert relation.target_label == (
        "lov om sikkerhed til søs, jf. lovbekendtgørelse nr. 1629 af 20. december 2016"
    )


def test_tekst_uden_indledning_har_ingen_hjemmel():
    assert extract_legal_basis("§ 1. Bekendtgørelsen gælder for skibe.") == []


def test_lovtitelnoeglen():
    assert law_title_key("Bekendtgørelse af lov om sikkerhed til søs") == "lov om sikkerhed til søs"
    assert law_title_key("Lov om havne") == "lov om havne"
    # En ændringslov er ikke selve loven.
    assert law_title_key("Lov om ændring af lov om sikkerhed til søs") is None
    assert law_title_key("Bekendtgørelse om redningsmidler") is None


# ---------------------------------------------------------------------------
# Udtræk: forgængere og ophævelser
# ---------------------------------------------------------------------------


def _pairs(relations):
    return [(r.relation_type, r.target_reference) for r in relations]


def test_lovbekendtgoerelse_afloeser_den_forrige():
    content = (
        "Hermed bekendtgøres lov om skibsudstyr, jf. lovbekendtgørelse nr. 700 af "
        "2. maj 2019, med de ændringer, der følger af lov nr. 5 af 3. januar 2022.\n"
        "§ 1. Loven gælder for danske skibe."
    )
    assert _pairs(extract_relations(title="x", content=content)) == [
        (RelationType.REPLACES, "LBK 700/2019")
    ]


def test_samtidig_ophaevet_bekendtgoerelse_er_forgaenger():
    content = (
        "§ 9. Bekendtgørelsen træder i kraft den 1. juli 2023.\n"
        "Stk. 2. Samtidig ophæves bekendtgørelse nr. 99 af 1. januar 2010 om skibsudstyr."
    )
    assert _pairs(extract_relations(title="x", content=content)) == [
        (RelationType.REPLACES, "BEK 99/2010")
    ]


def test_ophaevelsesakt_ophaever_uden_at_afloese():
    content = (
        "§ 1. Følgende bekendtgørelser ophæves:\n"
        "1) Bekendtgørelse nr. 301 af 1. marts 2004 om redningsmidler,\n"
        "2) Bekendtgørelse nr. 302 af 2. marts 2004 om brandsikkerhed."
    )
    assert _pairs(extract_relations(title="x", content=content, is_repeal_act=True)) == [
        (RelationType.REPEALS, "BEK 301/2004"),
        (RelationType.REPEALS, "BEK 302/2004"),
    ]


def test_aendringsforskrift_ophaever_paragraffer_ikke_dokumenter():
    content = (
        "§ 1. I bekendtgørelse nr. 101 af 14. april 2023 om brandsikkerhed foretages "
        "følgende ændringer:\n1) § 3 ophæves."
    )
    assert [r for r in extract_relations(title="x", content=content)
            if r.relation_type != RelationType.EMPOWERED_BY] == []


def test_teksten_er_ikke_sin_egen_forgaenger():
    content = "Stk. 2. Samtidig ophæves bekendtgørelse nr. 99 af 1. januar 2010."
    assert extract_relations(title="x", content=content, own_keys={"BEK 99/2010"}) == []


def test_kildemetadata_laeses_defensivt():
    raw = {"fields": {
        "Erstatter": "https://www.retsinformation.dk/eli/lta/2016/1629",
        "AfloestAf": "B20240012305",
        "titel": "uden betydning",
    }}
    assert _pairs(extract_relations(title="x", content="", raw_metadata=raw)) == [
        (RelationType.REPLACES, "1629/2016"),
        (RelationType.REPLACED_BY, "accn:B20240012305"),
    ]


# ---------------------------------------------------------------------------
# Ende-til-ende: import, opslag og API
# ---------------------------------------------------------------------------


def _record(source_id, number, doc_type, published, title, content, status="Gældende"):
    return {
        "source_id": source_id,
        "retsinformation_id": source_id,
        "title": title,
        "short_title": None,
        "document_type": doc_type,
        "authority": "Søfartsstyrelsen",
        "ministry": "Erhvervsministeriet",
        "published_date": published,
        "effective_date": published,
        "status": status,
        "change_date": published,
        "document_number": number,
        "keywords": [],
        "source_url": f"https://example.invalid/fixture/{source_id}",
        "content": content,
    }


LAW_TITLE = "Bekendtgørelse af lov om skibsudstyr"
LAW_BODY = (
    "§ 1. Loven gælder for danske skibe og deres udstyr.\n"
    "§ 2. Skibe skal være forsynet med godkendt udstyr.\n"
    "§ 3. Erhvervsministeren fastsætter regler om skibes udstyr.\n"
    "Stk. 2. Ministeren kan fastsætte regler om redningsmidler på skibe."
)

# Nyeste først: henvisningerne må vente på, at deres mål importeres.
CHAIN = [
    _record("TEST-BEK-2023-1234", "1234", "BEK", "2023-07-01",
            "Bekendtgørelse om redningsmidler på skibe",
            "I medfør af § 3, stk. 2, i lov om skibsudstyr, jf. lovbekendtgørelse nr. 700 "
            "af 2. maj 2019, fastsættes:\n"
            "§ 1. Skibe skal have redningsmidler til alle om bord.\n"
            "§ 9. Bekendtgørelsen træder i kraft den 1. juli 2023.\n"
            "Stk. 2. Samtidig ophæves bekendtgørelse nr. 99 af 1. januar 2010 om redningsmidler."),
    _record("TEST-LBK-2023-0900", "900", "LBK", "2023-03-01", LAW_TITLE,
            "Hermed bekendtgøres lov om skibsudstyr, jf. lovbekendtgørelse nr. 700 af "
            "2. maj 2019, med de ændringer, der følger af lov nr. 5 af 3. januar 2022.\n"
            + LAW_BODY),
    _record("TEST-LBK-2019-0700", "700", "LBK", "2019-05-02", LAW_TITLE,
            "Hermed bekendtgøres lov om skibsudstyr, jf. lovbekendtgørelse nr. 500 af "
            "1. marts 2014, med de ændringer, der følger af lov nr. 3 af 2. januar 2018.\n"
            + LAW_BODY, status="Historisk"),
    _record("TEST-LBK-2014-0500", "500", "LBK", "2014-03-01", LAW_TITLE, LAW_BODY,
            status="Historisk"),
]


def _fixture_records() -> list[dict]:
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return list(data if isinstance(data, list) else data.get("documents", data))


@pytest.fixture()
def imported(seeded_session):
    ImportService(
        seeded_session,
        client=FixtureRetsinformationClient(documents=CHAIN + _fixture_records()),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()
    return seeded_session


def _doc(session, number: str, year: int) -> Document:
    return next(
        d for d in session.scalars(select(Document).where(Document.document_number == number))
        if d.published_date.year == year
    )


def test_importen_gemmer_relationer_og_knytter_ventende(imported):
    bek = _doc(imported, "1234", 2023)
    rows = imported.scalars(
        select(DocumentRelation).where(DocumentRelation.source_document_id == bek.id)
    ).all()
    by_type = {(r.relation_type, r.specific_section): r for r in rows}

    basis = by_type[(RelationType.EMPOWERED_BY, "§ 3, stk. 2")]
    # Importeret FØR LBK 700, men knyttet, da LBK 700 kom ind.
    assert basis.target_document_id == _doc(imported, "700", 2019).id

    predecessor = by_type[(RelationType.REPLACES, None)]
    assert predecessor.target_document_id is None
    assert predecessor.target_reference == "BEK 99/2010"


def test_lovens_historik_foelges_over_flere_led(imported):
    newest = _doc(imported, "900", 2023)
    relations = RelationRepository(imported).read(newest)
    assert [(p.document_number, p.year) for p in relations.predecessors] == [
        ("500", 2014), ("700", 2019),
    ]
    assert relations.successors == []

    oldest = _doc(imported, "500", 2014)
    successors = RelationRepository(imported).read(oldest).successors
    assert [s.document_number for s in successors] == ["700", "900"]


def test_tilknyttede_bekendtgoerelser_gaelder_hele_loven(imported):
    """BEK 1234 er udstedt i medfør af LBK 700, men loven er siden samlet på
    ny i LBK 900. Bemyndigelsen står stadig i loven — også i dens nyeste
    version."""
    for number, year in [("900", 2023), ("700", 2019)]:
        delegated = RelationRepository(imported).read(_doc(imported, number, year))
        assert [(d.document_number, d.section) for d in delegated.delegated_regulations] == [
            ("1234", "§ 3, stk. 2"),
        ]


def test_hjemmel_uden_nummer_findes_paa_lovens_titel(imported):
    """Fixtures: "I medfør af § 24 b i lov om sikkerhed til søs" peger på
    "Bekendtgørelse af lov om sikkerhed til søs" (LBK 33)."""
    law = _doc(imported, "33", 2023)
    delegated = RelationRepository(imported).read(law).delegated_regulations
    pairs = {(d.document_number, d.section) for d in delegated}
    assert {("710", "§ 24 b"), ("512", "§ 1"), ("512", "§ 17")} <= pairs


def test_api_dokument_med_historik_og_tilknyttede(imported, api_client):
    newest = _doc(imported, "900", 2023)
    body = api_client.get(f"/api/documents/{newest.id}").json()

    assert [p["label"] for p in body["history"]["predecessors"]] == [
        "LBK nr. 500 af 2014", "LBK nr. 700 af 2019",
    ]
    assert body["history"]["successors"] == []
    [delegated] = body["delegated_regulations"]
    assert delegated["document_number"] == "1234"
    assert delegated["section"] == "§ 3, stk. 2"
    assert delegated["id"] == _doc(imported, "1234", 2023).id


def test_api_bekendtgoerelse_med_hjemmel_og_ukendt_forgaenger(imported, api_client):
    bek = _doc(imported, "1234", 2023)
    body = api_client.get(f"/api/documents/{bek.id}").json()

    [basis] = body["legal_basis"]
    assert basis["id"] == _doc(imported, "700", 2019).id
    assert basis["sections"] == ["§ 3, stk. 2"]
    assert basis["title"].startswith("lov om skibsudstyr")

    [predecessor] = body["history"]["predecessors"]
    assert predecessor["id"] is None
    assert predecessor["year"] == 2010
    assert "nr. 99" in predecessor["label"]


def test_api_dokument_uden_relationer_har_tomme_felter(imported, api_client):
    doc = _doc(imported, "101", 2023)
    body = api_client.get(f"/api/documents/{doc.id}").json()
    assert body["history"] == {"predecessors": [], "successors": []}
    assert body["delegated_regulations"] == []


def test_cli_genopbygger_relationer_for_eksisterende_dokumenter(imported, capsys):
    """Produktion: dokumenter importeret før migration 0010 har ingen
    relationer. `relations rebuild` skal give det samme som en import."""
    from sqlalchemy import delete

    from app.cli import main

    def snapshot():
        return sorted(
            (r.source_document_id, r.relation_type, r.target_reference,
             r.target_document_id, r.specific_section)
            for r in imported.scalars(select(DocumentRelation))
        )

    after_import = snapshot()
    assert after_import

    imported.execute(delete(DocumentRelation))
    imported.commit()
    assert snapshot() == []

    assert main(["relations", "rebuild"]) == 0
    imported.expire_all()
    assert snapshot() == after_import
    assert "empowered_by" in capsys.readouterr().out

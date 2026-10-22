"""To-spaltede traktater (BKI): engelsk original og dansk oversættelse side om side.

XML'en her er KONSTRUERET efter det trykte layout — én tabelrække pr. trykt
linje, engelsk til venstre og dansk til højre — fordi Retsinformations
egentlige skema ikke er verificeret (se ``xml_parser``). Teksten er et
uddrag i stil med AFS-konventionen; den er ikke en gengivelse af BKI nr. 39
af 2008.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.text import content_hash
from app.models import Document, DocumentRelation
from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import ImportService
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient
from app.services.retsinformation.parallel_text import join_column_lines, language_score
from app.services.retsinformation.xml_parser import parse_document_xml

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "documents.json"

#: (engelsk linje, dansk linje) — som de står side om side på siden.
PARALLEL_ROWS = [
    ("INTERNATIONAL CONVENTION ON THE CON-", "INTERNATIONAL KONVENTION OM BE-"),
    ("TROL OF HARMFUL ANTI-FOULING SYS-", "GRÆNSNING AF SKADELIGE BUNDMA-"),
    ("TEMS ON SHIPS, 2001", "LINGSSYSTEMER PÅ SKIBE, 2001"),
    ("", ""),
    ("THE PARTIES TO THIS CONVENTION,", "PARTERNE I DENNE KONVENTION,"),
    ("NOTING that scientific studies and investi-", "SOM BEMÆRKER, at videnskabelige under-"),
    ("gations by Governments and competent in-", "søgelser foretaget af regeringer og kompe-"),
    ("ternational organizations have shown that", "tente internationale organisationer har vist,"),
    ("certain anti-fouling systems used on ships", "at visse bundmalingssystemer, der anvendes på"),
    ("pose a substantial risk of toxicity to the", "skibe, udgør en betydelig risiko for toksicitet"),
    ("marine environment,", "for havmiljøet,"),
    ("HAVE AGREED as follows:", "ER BLEVET ENIGE om følgende:"),
    ("Article 1", "Artikel 1"),
    ("General Obligations", "Almindelige forpligtelser"),
    ("(1) Each Party to this Convention under-", "(1) Hver part i denne konvention forpligter"),
    ("takes to give full and complete effect to its", "sig til at gennemføre konventionens bestem-"),
    ("provisions in order to reduce or eliminate", "melser fuldt ud for at begrænse eller fjerne"),
    ("adverse effects on the marine environment", "skadelige virkninger på havmiljøet og menne-"),
    ("and human health caused by anti-fouling", "skers sundhed forårsaget af bundmalings-"),
    ("systems.", "systemer."),
    ("(2) The Annexes form an integral part of this", "(2) Bilagene udgør en integreret del af denne"),
    ("Convention, and the SOLAS Convention ap-", "konvention, og SOLAS-"),
    ("plies to the ships of the Party.", "konventionen og skibs- og havnesikkerhed gælder."),
]


def _xml(rows, *, swap: bool = False) -> str:
    def cell(text):
        return f"<td><Char>{text}</Char></td>"

    body = "".join(
        f"<tr>{cell(da) + cell(en) if swap else cell(en) + cell(da)}</tr>" for en, da in rows
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Dokument>
  <Meta>
    <DocumentType>BKI</DocumentType>
    <DocumentTitle>Bekendtgørelse af international konvention om begrænsning af skadelige bundmalingssystemer på skibe, 2001</DocumentTitle>
    <Nummer>39</Nummer>
  </Meta>
  <DokumentIndhold>
    <Linea>Den 5. oktober 2001 vedtoges i London en international konvention om begrænsning af skadelige bundmalingssystemer på skibe.</Linea>
    <table>{body}</table>
    <Linea>Udenrigsministeriet, den 30. september 2008.</Linea>
  </DokumentIndhold>
</Dokument>"""


# ---------------------------------------------------------------------------
# Parseren
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parsed():
    return parse_document_xml(_xml(PARALLEL_ROWS))


def test_dansk_tekst_er_ren_dansk(parsed):
    content = parsed.content
    assert "INTERNATIONAL KONVENTION OM BEGRÆNSNING AF SKADELIGE BUNDMALINGSSYSTEMER PÅ SKIBE, 2001" in content
    assert "videnskabelige undersøgelser foretaget af regeringer og kompetente internationale" in content
    # Ingen engelske tekstbrokker.
    for english in ("CONVENTION ON THE", "TROL OF", "NOTING", "Each Party", "shall", "Article 1"):
        assert english not in content
    assert language_score(content) < -0.5


def test_dansk_tekst_bevarer_indledning_og_underskrift(parsed):
    lines = parsed.content.splitlines()
    assert lines[0].startswith("Den 5. oktober 2001 vedtoges i London")
    assert lines[-1] == "Udenrigsministeriet, den 30. september 2008."


def test_engelsk_tekst_er_den_autentiske_ordlyd(parsed):
    english = parsed.content_en
    assert english.startswith(
        "INTERNATIONAL CONVENTION ON THE CONTROL OF HARMFUL ANTI-FOULING SYSTEMS ON SHIPS, 2001"
    )
    assert "investigations by Governments and competent international organizations" in english
    assert "Each Party to this Convention undertakes to give full and complete effect" in english
    for danish in ("KONVENTION OM", "videnskabelige", "Hver part", "Artikel"):
        assert danish not in english
    assert language_score(english) > 0.5


def test_orddeling_fjernes_men_aegte_bindestreger_bevares(parsed):
    assert "CON-" not in parsed.content_en and "BE-" not in parsed.content
    assert "ANTI-FOULING" in parsed.content_en  # står også med bindestreg midt i en linje
    assert "SOLAS-konventionen" in parsed.content  # forkortelse + sammensætning
    assert "skibs- og havnesikkerhed" in parsed.content  # fælles efterled
    assert "SOLAS Convention applies" in parsed.content_en


def test_artikler_og_overskrifter_bliver_egne_afsnit(parsed):
    lines = parsed.content_en.splitlines()
    assert "Article 1" in lines
    assert "General Obligations" in lines
    assert any(line.startswith("(1) Each Party") for line in lines)
    assert any(line.startswith("(2) The Annexes") for line in lines)


def test_praeamblens_betragtninger_og_tomme_raekker_giver_afsnit(parsed):
    english = parsed.content_en.splitlines()
    danish = parsed.content.splitlines()
    assert english[1] == "THE PARTIES TO THIS CONVENTION,"  # efter tom række
    assert any(line.startswith("NOTING that") for line in english)
    assert "HAVE AGREED as follows:" in english
    assert any(line.startswith("SOM BEMÆRKER, at") for line in danish)
    assert "ER BLEVET ENIGE om følgende:" in danish


def test_spalterne_genkendes_paa_sproget_ikke_positionen():
    swapped = parse_document_xml(_xml(PARALLEL_ROWS, swap=True))
    reference = parse_document_xml(_xml(PARALLEL_ROWS))
    assert swapped.content == reference.content
    assert swapped.content_en == reference.content_en
    assert swapped.raw_metadata["parallel_text"]["english_side"] == ["right"]


def test_almindelig_dansk_tabel_med_to_spalter_roeres_ikke():
    rows = [
        ("Skibstype", "Antal redningsflåder skal være mindst"),
        ("Passagerskib", "to for hver side af skibet og en reserve"),
        ("Lastskib", "en for hver side af skibet, der er godkendt"),
        ("Fiskeskib", "en, der er godkendt til alle om bord og til den"),
    ]
    parsed = parse_document_xml(_xml(rows))
    assert parsed.content_en == ""
    assert "parallel_text" not in parsed.raw_metadata
    assert "Passagerskib" in parsed.content


def test_dokument_uden_tabel_har_ingen_engelsk_tekst():
    parsed = parse_document_xml(
        "<Dokument><DokumentIndhold><Linea>§ 1. Skibe skal have redningsmidler til "
        "alle om bord.</Linea></DokumentIndhold></Dokument>"
    )
    assert parsed.content_en == ""


def test_join_column_lines_tom_spalte():
    assert join_column_lines(["", "  "]) == ""


# ---------------------------------------------------------------------------
# Import: to dokumenter bundet med translation_of
# ---------------------------------------------------------------------------


def _record(source_id, number, doc_type, title, content, content_en=""):
    return {
        "source_id": source_id,
        "retsinformation_id": source_id,
        "title": title,
        "short_title": None,
        "document_type": doc_type,
        "authority": "Udenrigsministeriet",
        "ministry": "Udenrigsministeriet",
        "published_date": "2008-09-30",
        "effective_date": "2008-09-17",
        "status": "Gældende",
        "change_date": "2008-09-30",
        "document_number": number,
        "keywords": [],
        "source_url": f"https://example.invalid/fixture/{source_id}",
        "content": content,
        "content_en": content_en,
    }


BKI_TITLE = (
    "Bekendtgørelse af international konvention om begrænsning af skadelige "
    "bundmalingssystemer på skibe, 2001"
)
#: Sådan så den gemte tekst ud før: spalterne læst vandret, linje for linje.
INTERLEAVED = "\n".join(f"{en} {da}" for en, da in PARALLEL_ROWS if en)


def _fixture_records() -> list[dict]:
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return list(data if isinstance(data, list) else data.get("documents", data))


def _import(session, records):
    return ImportService(
        session,
        client=FixtureRetsinformationClient(documents=records),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()


@pytest.fixture()
def imported(seeded_session):
    clean = parse_document_xml(_xml(PARALLEL_ROWS))
    _import(seeded_session, _fixture_records() + [
        _record("TEST-BKI-2008-0039", "39", "BKI", BKI_TITLE, clean.content, clean.content_en)
    ])
    return seeded_session


def _bki(session) -> Document:
    return session.scalars(select(Document).where(Document.source_id == "TEST-BKI-2008-0039")).one()


def _english(session) -> Document:
    return session.scalars(
        select(Document).where(Document.source_id == "TEST-BKI-2008-0039:en")
    ).one()


def test_engelsk_tekst_bliver_eget_dokument(imported):
    danish, english = _bki(imported), _english(imported)

    assert danish.language == "da"
    assert danish.document_type == "Bekendtgørelse af international overenskomst"
    assert "CONVENTION" not in danish.current_version.content

    assert english.language == "en"
    assert english.document_type == "TREATY"
    assert english.source == danish.source
    assert english.title == (
        "International Convention on the Control of Harmful Anti-Fouling Systems on Ships, 2001"
    )
    assert english.current_version.content.startswith(
        "INTERNATIONAL CONVENTION ON THE CONTROL"
    )
    # Nummeret tilhører den danske kundgørelse: "BKI 39" skal give ét dokument.
    assert english.document_number is None
    assert english.retsinformation_id is None
    # Samme instrument: relevansen arves, så dokumentet ikke afvises.
    assert english.maritime_score == danish.maritime_score


def test_dokumenterne_bindes_med_translation_of(imported):
    danish, english = _bki(imported), _english(imported)
    [relation] = imported.scalars(
        select(DocumentRelation).where(DocumentRelation.relation_type == "translation_of")
    ).all()
    assert (relation.source_document_id, relation.target_document_id) == (danish.id, english.id)


def test_engelsk_dokument_er_soegbart_paa_engelsk(imported):
    from app.services.search import SearchQuery, get_search_backend

    results = get_search_backend(imported, "lexical").search(
        imported, SearchQuery(q="anti-fouling convention", mode="lexical")
    )
    assert _english(imported).id in [hit.document.id for hit in results.hits]


def test_genopbygning_af_relationer_bevarer_oversaettelsen(imported):
    from app.cli import main

    assert main(["relations", "rebuild"]) == 0
    imported.expire_all()
    assert imported.scalars(
        select(DocumentRelation).where(DocumentRelation.relation_type == "translation_of")
    ).one().target_document_id == _english(imported).id


def test_versioner_er_uaendrede_for_almindelige_dokumenter(imported):
    other = imported.scalars(select(Document).where(Document.document_number == "101")).first()
    assert other.language == "da"
    assert other.current_version.content_hash == content_hash(other.current_version.content)


def test_api_sprogversioner_i_begge_retninger(imported, api_client):
    danish, english = _bki(imported), _english(imported)

    body = api_client.get(f"/api/documents/{danish.id}").json()
    assert body["language"] == "da"
    assert body["has_english_version"] is True
    assert [(t["id"], t["language"]) for t in body["translations"]] == [(english.id, "en")]
    assert "content_en" not in body
    assert "CONVENTION" not in body["content"]

    body = api_client.get(f"/api/documents/{english.id}").json()
    assert body["language"] == "en"
    assert body["document_type"] == "TREATY"
    assert [(t["id"], t["language"]) for t in body["translations"]] == [(danish.id, "da")]
    assert body["content"].startswith("INTERNATIONAL CONVENTION ON THE CONTROL")


def test_api_uden_engelsk_version(imported, api_client):
    other = imported.scalars(select(Document).where(Document.document_number == "101")).first()
    body = api_client.get(f"/api/documents/{other.id}").json()
    assert body["has_english_version"] is False
    assert body["translations"] == []


# ---------------------------------------------------------------------------
# reparse
# ---------------------------------------------------------------------------


def test_reparse_skiller_sprogene_ad_i_to_dokumenter(seeded_session, monkeypatch, capsys):
    """Før: den gemte tekst er spalterne læst vandret, og der er intet
    engelsk dokument. Efter `reparse --doc-type BKI`: ny dansk version med
    ren dansk tekst, et engelsk dokument og relationen imellem dem. Andre
    dokumenter hentes ikke."""
    import app.cli as cli

    old = _record("TEST-BKI-2008-0039", "39", "BKI", BKI_TITLE, INTERLEAVED)
    _import(seeded_session, _fixture_records() + [old])
    assert "CON- INTERNATIONAL KONVENTION OM BE-" in _bki(seeded_session).current_version.content
    assert seeded_session.scalars(
        select(Document).where(Document.language == "en")
    ).all() == []

    clean = parse_document_xml(_xml(PARALLEL_ROWS))
    fetched: list[str] = []

    class Recording(FixtureRetsinformationClient):
        def get_document(self, document_id):
            fetched.append(document_id)
            return super().get_document(document_id)

    fresh = _record("TEST-BKI-2008-0039", "39", "BKI", BKI_TITLE, clean.content, clean.content_en)
    monkeypatch.setattr(
        cli, "build_source_client",
        lambda *a, **k: Recording(documents=_fixture_records() + [fresh]),
    )

    assert cli.main(["reparse", "--doc-type", "BKI", "--source", "fixture"]) == 0
    assert fetched == ["TEST-BKI-2008-0039"]

    seeded_session.expire_all()
    danish = _bki(seeded_session)
    assert danish.current_version.version_number == 2
    assert "CONVENTION" not in danish.current_version.content
    english = _english(seeded_session)
    assert english.current_version.content.startswith("INTERNATIONAL CONVENTION ON THE CONTROL")
    assert seeded_session.scalars(
        select(DocumentRelation.target_document_id).where(
            DocumentRelation.source_document_id == danish.id,
            DocumentRelation.relation_type == "translation_of",
        )
    ).one() == english.id
    assert "Opdateret    : 1" in capsys.readouterr().out


def test_reparse_toerkoersel_henter_intet(imported, monkeypatch, capsys):
    import app.cli as cli

    def fail(*args, **kwargs):
        raise AssertionError("tørkørsel må ikke hente")

    monkeypatch.setattr(FixtureRetsinformationClient, "get_document", fail)
    assert cli.main(["reparse", "--doc-type", "BKI", "--source", "fixture", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "TEST-BKI-2008-0039" in out and "tørkørsel" in out
    # Det engelske dokument er ikke en BKI og genhentes ikke for sig selv.
    assert "TEST-BKI-2008-0039:en" not in out

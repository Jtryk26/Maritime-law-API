"""Test af kildeklienter og kildevalg.

Vigtigste krav: produktionsklienten må aldrig stiltiende erstattes af
syntetiske data.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.services.retsinformation import (
    FixtureRetsinformationClient,
    ProductionRetsinformationClient,
    UnknownSourceClientError,
    build_source_client,
)
from app.services.retsinformation.base import (
    DocumentNotFoundError,
    PermanentSourceError,
    TransientSourceError,
)
from app.services.retsinformation.normalization import (
    map_document_type,
    map_status,
    parse_danish_date,
)
from app.services.retsinformation.xml_parser import parse_document_xml
from tests.conftest import FIXTURE_TOTAL, FIXTURE_TOTAL_REV2


# ---------------------------------------------------------------------------
# Kildevalg
# ---------------------------------------------------------------------------


def test_fixture_vaelges_eksplicit():
    client = build_source_client("fixture")
    assert client.kind == "fixture"


def test_production_vaelges_eksplicit():
    client = build_source_client("production")
    assert client.kind == "production"
    client.close()


def test_ukendt_kilde_afvises_uden_fallback():
    """Der må ikke vælges en standard — en tastefejl kunne ellers
    føre til at syntetiske data blev importeret i drift."""
    with pytest.raises(UnknownSourceClientError):
        build_source_client("produktion")
    with pytest.raises(UnknownSourceClientError):
        build_source_client("")


# ---------------------------------------------------------------------------
# Fixture-klienten
# ---------------------------------------------------------------------------


def test_fixture_markerer_alle_dokumenter_som_syntetiske():
    client = FixtureRetsinformationClient(revision=1)
    for ref in client.get_documents():
        assert client.get_document(ref.source_id).is_synthetic is True


def test_fixture_indeholder_baade_maritime_og_ikke_maritime():
    client = FixtureRetsinformationClient(revision=1)
    titler = [r.title.lower() for r in client.get_documents()]
    assert len(titler) == FIXTURE_TOTAL
    assert any("passagerskibe" in t for t in titler)
    assert any("folkeskolens" in t for t in titler)


def test_revision_2_overskriver_revision_1():
    rev1 = FixtureRetsinformationClient(revision=1)
    rev2 = FixtureRetsinformationClient(revision=2)

    assert len(rev2.get_documents()) == FIXTURE_TOTAL_REV2  # ét nyt dokument
    assert rev1.get_document("FIXT-BEK-2019-0999").status == "Historisk"
    assert rev2.get_document("FIXT-BEK-2019-0999").status == "Ophævet"
    assert "termisk kamera" in rev2.get_document("FIXT-BEK-2023-0101").content
    assert "termisk kamera" not in rev1.get_document("FIXT-BEK-2023-0101").content


def test_ukendt_fixturdokument_giver_not_found():
    with pytest.raises(DocumentNotFoundError):
        FixtureRetsinformationClient(revision=1).get_document("FINDES-IKKE")


# ---------------------------------------------------------------------------
# Normalisering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,forventet",
    [
        ("BEK", "Bekendtgørelse"),
        ("BEK H", "Bekendtgørelse"),  # historisk variant
        ("LBK", "Lovbekendtgørelse"),
        ("LOV", "Lov"),
        ("VEJ", "Vejledning"),
        ("Bekendtgørelse", "Bekendtgørelse"),
        (None, None),
    ],
)
def test_dokumenttyper_normaliseres(raw, forventet):
    assert map_document_type(raw) == forventet


@pytest.mark.parametrize(
    "raw,forventet",
    [
        ("Valid", "Gældende"),
        # Kildens egen skrivemåde. Slap tidligere igennem uoversat.
        ("Historic", "Historisk"),
        ("historisk", "Historisk"),
        ("Ophævet", "Ophævet"),
        (None, None),
    ],
)
def test_status_normaliseres(raw, forventet):
    assert map_status(raw) == forventet


@pytest.mark.parametrize(
    "raw,forventet",
    [
        ("2024-03-12", date(2024, 3, 12)),
        ("12-03-2024", date(2024, 3, 12)),
        ("2024-03-12T08:30:00Z", date(2024, 3, 12)),
        ("30. september 2024", date(2024, 9, 30)),
        ("ikke en dato", None),
        (None, None),
    ],
)
def test_datoer_fortolkes(raw, forventet):
    assert parse_danish_date(raw) == forventet


# ---------------------------------------------------------------------------
# XML-parseren
# ---------------------------------------------------------------------------


def test_xml_parser_udtraekker_felter():
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <Document>
      <Metadata>
        <Title>Bekendtgørelse om skibssikkerhed</Title>
        <DocumentType>BEK</DocumentType>
        <Myndighed>Søfartsstyrelsen</Myndighed>
        <PublicationDate>2024-01-15</PublicationDate>
        <Status>Valid</Status>
      </Metadata>
      <DocumentContents>
        <p>§ 1. Skibet skal være sødygtigt og forsvarligt bemandet.</p>
        <p>§ 2. Søfartsstyrelsen fører tilsyn.</p>
      </DocumentContents>
    </Document>"""

    parsed = parse_document_xml(xml)
    assert parsed.title == "Bekendtgørelse om skibssikkerhed"
    assert parsed.authority == "Søfartsstyrelsen"
    assert parsed.document_type == "BEK"
    assert "sødygtigt" in parsed.content
    assert parsed.parse_mode == "xml"


def test_xml_parser_er_namespace_uafhaengig():
    xml = """<doc xmlns="http://example.org/eli">
      <title>Bekendtgørelse om lodspligt</title>
      <content>§ 1. Lodspligt gælder i danske farvande.</content>
    </doc>"""
    parsed = parse_document_xml(xml)
    assert parsed.title == "Bekendtgørelse om lodspligt"
    assert "farvande" in parsed.content


def test_xml_parser_falder_tilbage_paa_tekst_ved_ugyldig_xml():
    """En skemaændring hos kilden må give dårligere metadata,
    ikke et nedbrud i importen."""
    parsed = parse_document_xml("<html><body><p>§ 1. Skibet.</p><p>Ikke lukket")
    assert parsed.parse_mode == "fallback-text"
    assert "Skibet" in parsed.content


def test_xml_parser_haandterer_tom_input():
    assert parse_document_xml("").parse_mode == "empty"


# ---------------------------------------------------------------------------
# Produktionsklientens HTTP-adfærd
# ---------------------------------------------------------------------------


def _client(handler: httpx.MockTransport) -> ProductionRetsinformationClient:
    # min_request_interval=0 så testen ikke venter på rate limiteren.
    return ProductionRetsinformationClient(
        client=httpx.Client(transport=handler), min_request_interval=0, max_retries=2
    )


def test_aendringsfeed_oversaettes_til_referencer():
    payload = [
        {
            "documentId": 12345,
            "accessionsnummer": "B20240012345",
            "reasonForChange": "New",
            "changeDate": "2024-03-12T04:00:00",
            "documentType": {"shortName": "BEK", "id": 20},
            "href": "https://www.retsinformation.dk/eli/accn/B20240012345/xml",
        }
    ]
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    client = _client(transport)

    refs = client.get_documents()
    assert len(refs) == 1
    assert refs[0].source_id == "B20240012345"
    assert refs[0].document_type == "Bekendtgørelse"
    assert refs[0].change_date == date(2024, 3, 12)
    client.close()


def test_404_giver_document_not_found():
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    client = _client(transport)
    with pytest.raises(DocumentNotFoundError):
        client.get_document("B20240012345")
    client.close()


def test_400_udenfor_aabningstid_er_forbigaaende(monkeypatch):
    """Lukkevinduet 23:45-03:00 er planlagt, ikke en permanent fejl.

    Skal derfor give TransientSourceError (retryable i koeen), ikke
    PermanentSourceError (som ville markere posten endeligt FAILED og
    aldrig blive forsoegt igen, naar servicen genaabner).
    """
    monkeypatch.setattr(
        "app.services.retsinformation.production._within_service_hours",
        lambda now=None: False,
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(400, text="Closed"))
    client = _client(transport)
    with pytest.raises(TransientSourceError):
        client.get_documents()
    client.close()


def test_400_indenfor_aabningstid_er_permanent_fejl(monkeypatch):
    """Et 400 midt paa dagen er en rigtig fejl (fx forkert forespoergsel),
    ikke lukketid, og skal fortsat behandles som permanent."""
    monkeypatch.setattr(
        "app.services.retsinformation.production._within_service_hours",
        lambda now=None: True,
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(400, text="Bad request"))
    client = _client(transport)
    with pytest.raises(PermanentSourceError):
        client.get_documents()
    client.close()


def test_serverfejl_forsoeges_igen_og_giver_transient_fejl():
    forsoeg = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        forsoeg["n"] += 1
        return httpx.Response(503)

    client = ProductionRetsinformationClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_request_interval=0,
        max_retries=2,
    )
    with pytest.raises(TransientSourceError):
        client.get_documents()
    assert forsoeg["n"] == 2  # der blev forsøgt igen
    client.close()


def test_midlertidig_fejl_efterfulgt_af_succes():
    tilstand = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        tilstand["n"] += 1
        if tilstand["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=[])

    client = ProductionRetsinformationClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_request_interval=0,
        max_retries=3,
    )
    assert client.get_documents() == []
    client.close()


def test_lookback_begraenses_til_ti_dage():
    """Kilden tillader højst 10 kalenderdage tilbage."""
    kaldte_datoer: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        kaldte_datoer.append(dict(request.url.params).get("date", ""))
        return httpx.Response(200, json=[])

    client = ProductionRetsinformationClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_request_interval=0,
    )
    client.get_updated_documents(date(2000, 1, 1))
    assert len(kaldte_datoer) <= 11  # 10 dage tilbage + i dag
    client.close()


def test_eksplicitte_id_er_kan_hentes_direkte():
    """Eneste vej til ældre dokumenter, da feeden kun dækker 10 dage."""
    client = _client(httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    refs = client.get_documents(explicit_ids=["B20220122005"])
    assert len(refs) == 1
    assert refs[0].source_id == "B20220122005"
    assert refs[0].source_url.endswith("/eli/accn/B20220122005")
    client.close()

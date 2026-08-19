"""De FAKTISKE feltnavne i Retsinformations ELI-XML.

Hvorfor denne fil findes
========================
Parseren var skrevet mod gættede, danske elementnavne
(``PubliceringsDato``, ``Myndighed``, ``Ikrafttraedelsesdato``). Kilden
bruger andre navne. Resultatet var, at ``published_date``,
``effective_date`` og ``authority`` blev NULL i produktionen, selv om
værdierne stod i XML'en. Ingenting fejlede — felterne var bare tomme.

De hidtidige parsertests brugte parserens egne gæt som input og kunne
derfor aldrig fange fejlen. Testene her bruger kildens rigtige navne.

Om testdataene
==============
XML'en nedenfor er REKONSTRUERET ud fra kildens egne svar, kontrolleret
18.08.2026 på:

    https://www.retsinformation.dk/eli/accn/B19300001605/xml   (kun Meta)
    https://www.retsinformation.dk/eli/accn/B20240123405/xml   (fuldtekst)

Elementnavne, nesting, attributter og metadataværdier er gengivet som de
blev observeret. Brødteksten i fuldtekstdokumentet er derimod forkortet
og omskrevet — den er testdata, ikke en gengivelse af gældende ret, og
må ikke bruges som retskilde.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.legal.content_kind import (
    CONTENT_KIND_FULL_TEXT,
    CONTENT_KIND_METADATA_ONLY,
    CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN,
    classify_content,
)
from app.services.retsinformation.normalization import (
    map_document_type,
    map_status,
    parse_danish_date,
)
from app.services.retsinformation.xml_parser import parse_document_xml

# ---------------------------------------------------------------------------
# 1. Dokument uden brødtekst hos kilden
# ---------------------------------------------------------------------------

METADATA_ONLY_XML = """<?xml version="1.0" encoding="utf-8"?>
<Dokument>
  <Meta>
    <DocumentType>Bekendtgørelse</DocumentType>
    <Rank>B</Rank>
    <AccessionNumber>B19300001605</AccessionNumber>
    <DocumentId>CM023179</DocumentId>
    <UniqueDocumentId>64151</UniqueDocumentId>
    <DocumentTitle>Bekendtgørelse angaaende Regler for sejladsen gennem de uddybede Render ved &quot;Draget&quot;, &quot;Mejlgrunden&quot; og &quot;Løgstør grunde&quot; i Limfjorden.</DocumentTitle>
    <Year>1930</Year>
    <DiesSigni>1930-01-22</DiesSigni>
    <DateOfSubmit id="submit_1" />
    <StartDate REFid="submit_1">1997-09-24</StartDate>
    <EndDate REFid="submit_1">2017-12-08</EndDate>
    <Status>Historic</Status>
    <Number>16</Number>
    <AnnouncedIn>Lovtidende A</AnnouncedIn>
    <DiesEdicti>2002-04-08</DiesEdicti>
    <DateOfHistoricMark>1990-08-21</DateOfHistoricMark>
    <Concerns id="concerns_1" />
    <Ref_Accn REFid="concerns_1">A19880058729</Ref_Accn>
    <Ref_Af REFid="concerns_1">1988-09-29</Ref_Af>
    <Ref_Text REFid="concerns_1">Bekendtgørelse af lov om skibsfartens betryggelse</Ref_Text>
    <Subject>LODSTVANG</Subject>
    <Republished>Nej</Republished>
    <JournalNumber>Ingen</JournalNumber>
    <Ministry>Erhvervsministeriet</Ministry>
    <AdministrativeAuthority>Søfartsstyrelsen</AdministrativeAuthority>
  </Meta>
</Dokument>"""


class TestMetadataOnlyDokument:
    @pytest.fixture()
    def parsed(self):
        return parse_document_xml(METADATA_ONLY_XML)

    def test_titlen_kommer_fra_documenttitle(self, parsed):
        assert parsed.title.startswith("Bekendtgørelse angaaende Regler for sejladsen")

    def test_myndigheden_kommer_fra_administrativeauthority(self, parsed):
        """Feltet hed ikke <Myndighed>. Derfor var authority NULL."""
        assert parsed.authority == "Søfartsstyrelsen"
        assert parsed.ministry == "Erhvervsministeriet"

    def test_datoerne_findes(self, parsed):
        # DiesSigni er dokumentets egen dato, DiesEdicti kundgørelsen.
        assert parse_danish_date(parsed.published_date) == date(1930, 1, 22)
        assert parse_danish_date(parsed.effective_date) == date(1997, 9, 24)

    def test_nummer_og_status(self, parsed):
        assert parsed.document_number == "16"
        assert map_status(parsed.status) == "Historisk"
        assert parsed.journal_number == "Ingen"

    def test_subject_bliver_noegleord(self, parsed):
        assert "LODSTVANG" in parsed.keywords

    def test_metadata_gemmes_ikke_som_lovtekst(self, parsed):
        """Kernen i fejlen: uden dette blev <Meta> gemt som brødtekst.

        Så fik dokumentet et indhold, der lignede tekst, men var en
        opremsning af feltværdier — og korpusset så mere komplet ud,
        end det var.
        """
        assert parsed.content == ""
        assert parsed.content_kind == CONTENT_KIND_METADATA_ONLY
        assert "Lovtidende" not in parsed.content
        assert parsed.raw_metadata["source_had_body"] is False


# ---------------------------------------------------------------------------
# 2. Dokument MED brødtekst hos kilden
# ---------------------------------------------------------------------------

FULL_TEXT_XML = """<?xml version="1.0" encoding="utf-8"?>
<Dokument>
  <Meta>
    <DocumentType>BEK H#LOKDOK04</DocumentType>
    <Rank>B</Rank>
    <AccessionNumber>B20240123405</AccessionNumber>
    <DocumentId>DK000084</DocumentId>
    <DocumentTitle>Bekendtgørelse om brandsikkerhed i passagerskibe</DocumentTitle>
    <Year>2024</Year>
    <DiesSigni>2024-11-25</DiesSigni>
    <StartDate>2024-11-27</StartDate>
    <EndDate>2026-06-04</EndDate>
    <Status>Valid</Status>
    <Number>1234</Number>
    <AnnouncedIn>Lovtidende A</AnnouncedIn>
    <DiesEdicti>2024-11-27</DiesEdicti>
    <JournalNumber>Søfartsstyrelsen, j.nr. 2024-1234</JournalNumber>
    <Ministry>Erhvervsministeriet</Ministry>
    <AdministrativeAuthority>Søfartsstyrelsen</AdministrativeAuthority>
  </Meta>
  <TitelGruppe>
    <Titel>Bekendtgørelse om brandsikkerhed i passagerskibe</Titel>
  </TitelGruppe>
  <DokumentIndhold>
    <Indledning>I medfør af § 1 i lov om sikkerhed til søs fastsættes:</Indledning>
    <Kapitel>
      <KapitelNr>Kapitel 1</KapitelNr>
      <KapitelTitel>Anvendelsesområde</KapitelTitel>
      <ParagrafGruppe>
        <Paragraf>
          <ParagrafNr>§ 1.</ParagrafNr>
          <Tekst>Bekendtgørelsen gælder for passagerskibe med en bruttotonnage på 500 og derover.</Tekst>
        </Paragraf>
        <Paragraf>
          <ParagrafNr>§ 2.</ParagrafNr>
          <Tekst>Skibsføreren har ansvaret for brandberedskabet om bord.</Tekst>
        </Paragraf>
      </ParagrafGruppe>
    </Kapitel>
  </DokumentIndhold>
  <UnderskriftGruppe>
    <Sted>Søfartsstyrelsen, den 25. november 2024</Sted>
  </UnderskriftGruppe>
  <Bilag>
    <BilagNr>Bilag 1</BilagNr>
    <Tekst>Krav til fast anbragte brandslukningsanlæg i maskinrum.</Tekst>
  </Bilag>
</Dokument>"""


class TestFuldtekstDokument:
    @pytest.fixture()
    def parsed(self):
        return parse_document_xml(FULL_TEXT_XML)

    def test_meta_har_forrang_over_broedtekst(self, parsed):
        """<Meta><Number> må ikke udkonkurreres af et tal i teksten."""
        assert parsed.document_number == "1234"

    def test_dokumenttype_normaliseres_trods_suffiks(self, parsed):
        assert map_document_type(parsed.document_type) == "Bekendtgørelse"

    def test_datoer_og_myndighed(self, parsed):
        assert parse_danish_date(parsed.published_date) == date(2024, 11, 25)
        assert parse_danish_date(parsed.effective_date) == date(2024, 11, 27)
        assert parsed.authority == "Søfartsstyrelsen"

    def test_paragrafferne_er_med(self, parsed):
        assert "§ 1." in parsed.content
        assert "§ 2." in parsed.content
        assert parsed.content_kind == CONTENT_KIND_FULL_TEXT

    def test_bilag_gaar_ikke_tabt(self, parsed):
        """Bilag bærer ofte de tekniske krav. Tidligere blev kun
        <DokumentIndhold> taget med, og bilaget forsvandt."""
        assert "brandslukningsanlæg" in parsed.content

    def test_metadata_lander_ikke_i_teksten(self, parsed):
        assert "Lovtidende A" not in parsed.content
        assert "B20240123405" not in parsed.content


# ---------------------------------------------------------------------------
# 3. Klassifikationen selv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,source_had_body,forventet",
    [
        ("§ 1. Skibet skal være sødygtigt.", True, CONTENT_KIND_FULL_TEXT),
        ("§ 1. Skibet skal være sødygtigt.", None, CONTENT_KIND_FULL_TEXT),
        (
            "Cirkulæret træder i kraft den 1. januar og ophæver cirkulære nr. 4.",
            True,
            CONTENT_KIND_TEXT_WITHOUT_PARAGRAPH_SIGN,
        ),
        ("", False, CONTENT_KIND_METADATA_ONLY),
        # Kildens ord vejer tungest: har den ingen brødtekst, er en rest
        # af tekst stadig ikke lovtekst.
        ("Lovtidende A 1930", False, CONTENT_KIND_METADATA_ONLY),
        ("", None, "empty"),
        (None, None, "empty"),
    ],
)
def test_indholdsklassifikation(content, source_had_body, forventet):
    assert classify_content(content, source_had_body=source_had_body) == forventet

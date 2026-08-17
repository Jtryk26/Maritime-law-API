"""Værn mod den fejl, der gjorde 98,9 % af produktionsindekset ubrugeligt.

Hvad der skete
==============
`xml_parser._element_text` samlede et dokuments tekst med::

    normalize_whitespace(" ".join(element.itertext()))

`normalize_whitespace` klapper **alt** whitespace sammen — også
linjeskift. Hele lovteksten kom derfor ud som én linje. Strukturparserens
mønstre er forankret i linjestart, så den fandt hverken kapitler eller
paragraffer, og hvert dokument faldt tilbage til vilkårlige tekstvinduer.

Ingenting fejlede undervejs. Importen sagde "gennemført", indekset sagde
"100 % vektoriseret", og søgningen svarede. Kun fordelingen af
`unit_type` afslørede det: 30.253 `fragment` mod 331 `paragraph`.

Hvorfor testene ikke fangede det
================================
Fixturerne i `data/fixtures/documents.json` har rigtige linjeskift i
JSON-strengen og går aldrig gennem `parse_document_xml`. Hele
strukturparseren blev afprøvet på tekst, der aldrig havde været igennem
det led, der ødelagde den.

Testene her lukker netop det hul: de går gennem den rigtige XML-parser og
gennem tekst, der er lige så flad som den, produktionen har gemt.
"""

from __future__ import annotations

import pytest

from app.core.text import normalize_whitespace
from app.services.embedding.chunking import chunk_document
from app.services.legal import parse_legal_structure
from app.services.legal.structure import normalize_legal_text
from app.services.retsinformation.xml_parser import parse_document_xml

# ---------------------------------------------------------------------------
# 1. XML-parseren skal bevare kildens egen struktur
# ---------------------------------------------------------------------------

ELI_XML = """<?xml version="1.0" encoding="utf-8"?>
<Dokument>
  <Metadata>
    <Titel>Bekendtgørelse om brandsikkerhed i passagerskibe</Titel>
    <Myndighed>Søfartsstyrelsen</Myndighed>
  </Metadata>
  <DokumentIndhold>
    <Praeambel>I medfør af § 1, stk. 2, i lov om sikkerhed til søs fastsættes:</Praeambel>
    <Kapitel>
      <KapitelNr>Kapitel 1</KapitelNr>
      <KapitelTitel>Anvendelsesområde</KapitelTitel>
      <Paragraf>
        <ParagrafNr>§ 1.</ParagrafNr>
        <Tekst>Bekendtgørelsen gælder for passagerskibe. Skibet skal <i>altid</i> være sødygtigt.</Tekst>
        <Stk><StkNr>Stk. 2.</StkNr><Tekst>Reglerne gælder ikke for fiskeskibe.</Tekst></Stk>
      </Paragraf>
      <Paragraf>
        <ParagrafNr>§ 2.</ParagrafNr>
        <Tekst>Skibsføreren har ansvaret, jf. § 4.</Tekst>
      </Paragraf>
    </Kapitel>
    <Kapitel>
      <KapitelNr>Kapitel 2</KapitelNr>
      <KapitelTitel>Brandslukning</KapitelTitel>
      <Paragraf><ParagrafNr>§ 3.</ParagrafNr><Tekst>Der skal findes slukningsanlæg.</Tekst></Paragraf>
    </Kapitel>
  </DokumentIndhold>
</Dokument>"""


class TestXmlBevarerStruktur:
    @pytest.fixture()
    def parsed(self):
        return parse_document_xml(ELI_XML)

    def test_linjeskift_overlever_parsingen(self, parsed):
        """Kernen i fejlen. Uden linjeskift finder strukturparseren intet."""
        assert parsed.content.count("\n") > 0

    def test_kapitler_og_paragraffer_findes_igen(self, parsed):
        structure = parse_legal_structure(parsed.content)

        assert [p.paragraph_id for p in structure.paragraphs] == ["§ 1", "§ 2", "§ 3"]
        assert [c.title for c in structure.chapters] == ["Anvendelsesområde", "Brandslukning"]

    def test_stykkerne_bliver_paragraffer_ikke_fragmenter(self, parsed):
        types = {chunk.unit_type for chunk in chunk_document(parsed.content)}
        assert types == {"preamble", "paragraph"}

    def test_inline_markup_deler_ikke_en_saetning(self, parsed):
        """<i>altid</i> er ikke en ny bestemmelse — det er tre ord i én sætning."""
        assert "Skibet skal altid være sødygtigt." in parsed.content

    def test_krydshenvisning_i_teksten_bliver_ikke_en_paragraf(self, parsed):
        structure = parse_legal_structure(parsed.content)
        assert "§ 4" not in [p.paragraph_id for p in structure.paragraphs]


# ---------------------------------------------------------------------------
# 2. Allerede flad tekst skal stadig kunne parses
# ---------------------------------------------------------------------------
# Produktionens 3.411 dokumenter ER gemt fladt. Rettelsen af XML-parseren
# hjælper først ved næste import af et ændret dokument; den eksisterende
# tekst skal kunne reddes, som den ligger. Derfor er dette den vigtigste
# test i filen.

FLAD_TEKST = (
    "I medfør af § 1, stk. 2, og § 17 i lov om sikkerhed til søs, jf. "
    "lovbekendtgørelse nr. 72 af 17. januar 2014, fastsættes efter bemyndigelse: "
    "Kapitel 1 Anvendelsesområde § 1. Bekendtgørelsen finder anvendelse på "
    "passagerskibe i indenrigsfart. Stk. 2. Reglerne gælder ikke for "
    "fritidsfartøjer, jf. dog § 7. "
    "§ 2. Skibsføreren har ansvaret for efterlevelsen, jf. § 4, stk. 2, og § 5. "
    "Kapitel 2 Brandslukning § 3. Der skal forefindes slukningsanlæg i maskinrummet. "
    "Stk. 2. Anlægget efterses årligt, jf. bekendtgørelse nr. 101. "
    "§ 4. Søfartsstyrelsen fører tilsyn efter § 2. "
    "§ 5. Bekendtgørelsen træder i kraft den 1. juli 2024."
)


class TestFladTekst:
    @pytest.fixture()
    def structure(self):
        assert FLAD_TEKST.count("\n") == 0, "testen måler intet, hvis teksten ikke er flad"
        return parse_legal_structure(FLAD_TEKST)

    def test_alle_paragraffer_findes(self, structure):
        assert [p.paragraph_id for p in structure.paragraphs] == [
            "§ 1", "§ 2", "§ 3", "§ 4", "§ 5",
        ]

    def test_kapitlerne_findes_med_titel(self, structure):
        assert [(c.number, c.title) for c in structure.chapters] == [
            ("1", "Anvendelsesområde"),
            ("2", "Brandslukning"),
        ]

    def test_kapitlet_foelger_med_paragraffen(self, structure):
        by_id = {p.paragraph_id: p for p in structure.paragraphs}
        assert by_id["§ 2"].chapter_no == "1"
        assert by_id["§ 3"].chapter_no == "2"

    def test_praeamblen_findes(self, structure):
        assert structure.preamble.startswith("I medfør af")

    def test_stykker_findes(self, structure):
        assert [s.number for s in structure.paragraphs[0].subsections] == [2]

    def test_stykkerne_bliver_paragraffer(self):
        types = [chunk.unit_type for chunk in chunk_document(FLAD_TEKST)]
        assert types.count("paragraph") == 5
        assert "fragment" not in types


class TestHenvisningerBliverIkkeBestemmelser:
    """Det farlige ved at genkende åbnere midt i en linje.

    Bliver "jf. § 4" til en ny paragraf, flyttes lovtekst over i en
    bestemmelse, den ikke hører til — og et søgeresultat peger på et sted,
    hvor reglen ikke står. Det er værre end ingen struktur overhovedet.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "§ 1. Skibet skal være sødygtigt, jf. § 4. Rederiet fører tilsyn.",
            "§ 1. Reglerne i § 4 finder tilsvarende anvendelse.",
            "§ 1. Overtrædelse straffes efter § 9. Bøden fastsættes af retten.",
            "§ 1. Kravene følger af § 3, stk. 2, og § 4.",
            "§ 1. Der henvises til § 12. Skibsføreren har ansvaret.",
            "§ 1. Bestemmelsen gælder dog ikke § 8.",
            "I medfør af § 1, stk. 2, og § 17 i lov om sikkerhed til søs fastsættes:",
        ],
    )
    def test_kun_den_aegte_aabner_taeller(self, text):
        structure = parse_legal_structure(text)
        found = [p.paragraph_id for p in structure.paragraphs]
        assert found in ([], ["§ 1"]), f"{text!r} gav {found}"

    def test_faldende_nummerering_afvises(self):
        """Monotonicitet er det andet værn.

        Står "§ 3" inde i § 12, er 3 < 12, og kandidaten forkastes uanset
        hvad der står foran den.
        """
        text = "§ 12. Rederiet fører tilsyn. Bestemmelsen supplerer § 3. Skibet skal synes."
        structure = parse_legal_structure(text)
        assert [p.paragraph_id for p in structure.paragraphs] == ["§ 12"]

    def test_litra_paragraf_er_en_aegte_fortsaettelse(self):
        """§ 12 a følger efter § 12 — monotonicitet må ikke afvise den.

        Litra-paragraffer står netop dér, hvor lovgiver har indsat noget
        senere, og de er almindelige i den ældre del af samlingen.
        """
        text = "§ 12. Reglen gælder. § 12 a. Supplerende regel. § 13. Sidste regel."
        structure = parse_legal_structure(text)
        assert [p.paragraph_id for p in structure.paragraphs] == ["§ 12", "§ 12 a", "§ 13"]

    def test_nummer_uden_punktum_er_en_henvisning(self):
        text = "§ 1. Skibet skal opfylde § 4 og § 5 samt bilag 1."
        structure = parse_legal_structure(text)
        assert [p.paragraph_id for p in structure.paragraphs] == ["§ 1"]

    def test_lille_kapitel_i_henvisning_er_ikke_en_overskrift(self):
        text = (
            "Kapitel 1 Foranstaltninger § 1. Reglerne i retsplejelovens "
            "kapitel 74 om beslaglæggelse finder anvendelse. "
            "§ 2. Afgørelsen kan påklages."
        )
        structure = parse_legal_structure(text)

        assert [(c.number, c.title) for c in structure.chapters] == [
            ("1", "Foranstaltninger")
        ]
        assert [p.paragraph_id for p in structure.paragraphs] == ["§ 1", "§ 2"]
        assert all(p.chapter_no == "1" for p in structure.paragraphs)


class TestStruktureretTekstUaendret:
    """Segmenteringen må ikke ændre noget for tekst, der allerede er i orden."""

    STRUKTURERET = (
        "Kapitel 1\nAnvendelsesområde\n\n"
        "§ 1. Bekendtgørelsen gælder for skibe, jf. § 4.\n"
        "Stk. 2. Undtaget er fritidsfartøjer.\n\n"
        "Kapitel 2\nTilsyn\n\n"
        "§ 2. Søfartsstyrelsen fører tilsyn.\n"
    )

    def test_paragraffer_og_kapitler_er_som_foer(self):
        structure = parse_legal_structure(self.STRUKTURERET)
        assert [p.paragraph_id for p in structure.paragraphs] == ["§ 1", "§ 2"]
        assert [c.title for c in structure.chapters] == ["Anvendelsesområde", "Tilsyn"]

    def test_kapiteltitel_paa_samme_linje_forstaas(self):
        """Fejlen bagved: "\\d+\\s*[a-zA-Z]?" åd titlens første bogstav, så
        "Kapitel 1 Anvendelsesområde" blev til nummer "1 A"."""
        structure = parse_legal_structure(
            "Kapitel 1 Anvendelsesområde\n§ 1. Reglen gælder for skibe."
        )
        assert [(c.number, c.title) for c in structure.chapters] == [
            ("1", "Anvendelsesområde")
        ]

    def test_kapitelbogstav_bevares_naar_det_staar_alene(self):
        structure = parse_legal_structure("Kapitel 3 a\nSærlige regler\n§ 9. Reglen gælder.")
        assert structure.chapters[0].number == "3a"


class TestMetadataGraenser:
    def test_broedtekst_kan_ikke_gemmes_som_kapiteltitel(self):
        text = f"Kapitel 1 {'A' * 600} § 1. Reglen gælder for skibe."
        structure = parse_legal_structure(text)
        chunks = chunk_document(text)

        assert structure.chapters[0].title is None
        assert chunks[0].chapter_title is None
        assert all(
            chunk.section_title is None or len(chunk.section_title) <= 512 for chunk in chunks
        )


class TestInvarianterOgsaaPaaFladTekst:
    """Stykkerne skal stadig være sammenhængende udsnit uden huller.

    Segmenteringen indsætter ingen tegn — den flytter kun grænser — så
    positionerne skal pege uændret ind i teksten.
    """

    def test_hvert_stykke_er_et_udsnit_af_kilden(self):
        text = normalize_legal_text(FLAD_TEKST)
        for chunk in chunk_document(FLAD_TEKST):
            assert chunk.content == text[chunk.char_start : chunk.char_end].strip()

    def test_ingen_lovtekst_gaar_tabt(self):
        text = normalize_legal_text(FLAD_TEKST)
        covered = bytearray(len(text))
        for chunk in chunk_document(FLAD_TEKST):
            for position in range(chunk.char_start, min(chunk.char_end, len(text))):
                covered[position] = 1

        lost = [i for i, ch in enumerate(text) if not covered[i] and not ch.isspace()]
        assert not lost, f"{len(lost)} tegn ikke dækket, første ved {lost[0] if lost else '-'}"


class TestHeleSamlingenFladt:
    """Fixtursamlingen kørt gennem præcis den behandling, der ødelagde produktionen.

    Dette er den test, der ville have fanget fejlen. Den er skrevet ud fra
    symptomet: andelen af paragraf-stykker, målt på hele samlingen.
    """

    def _fixture_texts(self) -> list[str]:
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        payload = json.loads(
            (root / "data" / "fixtures" / "documents.json").read_text(encoding="utf-8")
        )
        return [d["content"] for d in payload["documents"] if d.get("content")]

    def test_paragrafandelen_holder_naar_teksten_er_flad(self):
        from collections import Counter

        counts: Counter = Counter()
        for text in self._fixture_texts():
            flat = normalize_whitespace(text)
            assert flat.count("\n") == 0
            for chunk in chunk_document(flat):
                counts[chunk.unit_type] += 1

        total = sum(counts.values())
        share = 100.0 * counts["paragraph"] / total
        assert share > 90.0, f"kun {share:.1f} % paragraffer: {dict(counts)}"

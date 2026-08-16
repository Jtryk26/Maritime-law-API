"""Strukturel parsing af lovtekst og normaliserede visningstitler.

Hvad der afprøves her, er ikke om parseren kan læse *en* velformateret
bekendtgørelse — det kan enhver regex. Det er om den holder de løfter,
resten af systemet bygger på:

* en paragraf er én enhed, med sit kapitel på,
* præamblen står for sig,
* tekst uden paragraffer giver en tom paragrafliste frem for at kaste,
* visningstitlen er kortere end originalen uden at være forkert.
"""

from __future__ import annotations

import pytest

from app.services.legal import (
    derive_display_title,
    paragraph_sort_key,
    parse_legal_structure,
    split_leading_type,
)

BEKENDTGOERELSE = """I medfør af § 1, stk. 2, og § 17 i lov om sikkerhed til søs, jf.
lovbekendtgørelse nr. 72 af 17. januar 2014, fastsættes efter bemyndigelse:

Kapitel 1
Anvendelsesområde

§ 1. Bekendtgørelsen finder anvendelse på passagerskibe i indenrigsfart.
Stk. 2. Reglerne gælder ikke for fritidsfartøjer.
Stk. 3. Søfartsstyrelsen kan fastsætte lempeligere krav.

§ 2. Skibsføreren har ansvaret for efterlevelsen.

Kapitel 2
Brandslukning

Fast installerede anlæg

§ 3. Der skal forefindes fast installerede brandslukningsanlæg i maskinrummet.

§ 3 a. Anlæggene skal efterses årligt.
"""


class TestStruktur:
    @pytest.fixture()
    def structure(self):
        return parse_legal_structure(BEKENDTGOERELSE)

    def test_paragrafferne_findes_i_raekkefoelge(self, structure):
        assert [p.paragraph_id for p in structure.paragraphs] == [
            "§ 1", "§ 2", "§ 3", "§ 3 a",
        ]

    def test_kapitlet_foelger_med_paragraffen(self, structure):
        by_id = {p.paragraph_id: p for p in structure.paragraphs}
        assert by_id["§ 1"].chapter_no == "1"
        assert by_id["§ 1"].chapter_title == "Anvendelsesområde"
        assert by_id["§ 3"].chapter_no == "2"
        assert by_id["§ 3"].chapter_title == "Brandslukning"

    def test_mellemoverskrift_fanges(self, structure):
        by_id = {p.paragraph_id: p for p in structure.paragraphs}
        assert by_id["§ 3"].heading == "Fast installerede anlæg"

    def test_stykker_hoerer_til_deres_paragraf(self, structure):
        first = structure.paragraphs[0]
        assert [s.number for s in first.subsections] == [2, 3]
        assert "fritidsfartøjer" in first.subsections[0].text
        # Stykkerne er en DEL af paragraffen, ikke noget ved siden af.
        assert "Stk. 2" in first.text

    def test_praeamblen_staar_for_sig(self, structure):
        assert "I medfør af" in structure.preamble
        assert structure.body.startswith("Kapitel 1")
        assert "I medfør af" not in structure.body

    def test_lovadresse_og_henvisning(self, structure):
        paragraph = structure.paragraph("§ 3")
        assert paragraph.legal_path == "Kapitel 2 — Brandslukning · § 3"
        assert paragraph.citation("Brandbekendtgørelsen") == (
            "Brandbekendtgørelsen § 3, kapitel 2"
        )

    def test_metadata_er_komplet(self, structure):
        meta = structure.paragraph("§ 3 a").to_metadata(document_title="X")
        assert meta["paragraph_id"] == "§ 3 a"
        assert meta["paragraph_sort_key"] == "0003a"
        assert meta["chapter_no"] == "2"


class TestSorteringsnoegle:
    def test_tocifrede_paragraffer_sorterer_efter_encifrede(self):
        keys = [paragraph_sort_key(n) for n in (1, 2, 10, 12)]
        assert keys == sorted(keys)

    def test_bogstav_sorterer_efter_tallet(self):
        assert paragraph_sort_key(12) < paragraph_sort_key(12, "a")
        assert paragraph_sort_key(12, "a") < paragraph_sort_key(13)


class TestRobusthed:
    def test_tom_tekst_giver_tom_struktur(self):
        structure = parse_legal_structure("")
        assert structure.paragraphs == []
        assert structure.has_paragraphs is False

    def test_tekst_uden_paragraffer_kaster_ikke(self):
        """Bilag, tabeller og vejledninger har ingen §§. Det er ikke en fejl."""
        structure = parse_legal_structure(
            "Bilag 1\n\nTabel over mindsteafstande\n\nA 12 m\nB 18 m\n"
        )
        assert structure.has_paragraphs is False
        assert structure.preamble == ""

    def test_krydshenvisning_bliver_ikke_en_ny_paragraf(self):
        """"jf. § 4" midt i en sætning må ikke starte en paragraf."""
        text = "§ 1. Skibet skal opfylde kravene i § 4, jf. § 7, og i bilag 1."
        structure = parse_legal_structure(text)
        assert [p.paragraph_id for p in structure.paragraphs] == ["§ 1"]

    def test_html_fjernes_foer_parsing(self):
        structure = parse_legal_structure(
            "<p>Kapitel 1</p><p>Formål</p><p>§ 1. Reglen gælder for skibe.</p>"
        )
        assert [p.paragraph_id for p in structure.paragraphs] == ["§ 1"]
        assert structure.paragraphs[0].chapter_title == "Formål"

    def test_kort_overskriftslinje_er_ikke_en_praeambel(self):
        """En enkelt overskrift må ikke ende bag et fold-ud som "præambel"."""
        structure = parse_legal_structure("Kapitel 1\nFormål\n\n§ 1. Reglen gælder.")
        assert structure.preamble == ""


class TestVisningstitel:
    def test_lovbekendtgoerelse_bliver_til_loven(self):
        assert derive_display_title(
            "Bekendtgørelse af lov om sikkerhed til søs"
        ) == "Lov om sikkerhed til søs"

    def test_populaernavn_i_parentes_fjernes(self):
        assert derive_display_title(
            "Bekendtgørelse af lov om sikkerhed til søs (søsikkerhedsloven)"
        ) == "Lov om sikkerhed til søs"

    def test_jf_hale_klippes(self):
        title = (
            "Bekendtgørelse af lov om sikkerhed til søs, jf. lovbekendtgørelse "
            "nr. 1629 af 17. december 2018 med senere ændringer"
        )
        assert derive_display_title(title) == "Lov om sikkerhed til søs"

    def test_kort_titel_efterlades_uroert(self):
        title = "Bekendtgørelse om brandsikkerhed i passagerskibe"
        assert derive_display_title(title) == title

    def test_lang_titel_klippes_ved_en_sproglig_graense(self):
        title = (
            "Bekendtgørelse om sikkerhed ved arbejdets udførelse på fiskeskibe, "
            "herunder krav til personlige værnemidler og fangstbehandlingsudstyr"
        )
        short = derive_display_title(title)

        assert len(short) < len(title)
        # Klippet falder ved kommaet, ikke midt i et ord.
        assert short == "Bekendtgørelse om sikkerhed ved arbejdets udførelse på fiskeskibe"
        assert not short.endswith(("…", " "))

    def test_korttitel_bruges_kun_naar_titlen_maatte_afkortes(self):
        """Kildens populærtitel er en nødudgang, ikke en forbedring.

        Skiftede visningstitlen navn hver gang kilden havde en kortere
        variant, ville resultatlisten vise noget andet end det, brugeren
        søgte på.
        """
        assert derive_display_title(
            "Bekendtgørelse om brandsikkerhed i passagerskibe",
            short_title="Brandbekendtgørelsen",
        ) == "Bekendtgørelse om brandsikkerhed i passagerskibe"

    def test_tom_titel_giver_tom_streng(self):
        assert derive_display_title(None) == ""
        assert derive_display_title("   ") == ""

    def test_typebetegnelse_kan_udskilles(self):
        assert split_leading_type("Bekendtgørelse om redningsmidler") == (
            "Bekendtgørelse om", "redningsmidler",
        )
        assert split_leading_type("Søloven") == (None, "Søloven")

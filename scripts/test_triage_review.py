"""Tests for regeltabellen i triage_review.py.

Kør: ``python -m pytest scripts/test_triage_review.py``

Testene låser de tilfælde, hvor rækkefølgen mellem grupperne betyder noget.
Uden dem kan en tilføjet regel stille flytte 55 fiskeridokumenter til den
forkerte kategori, uden at nogen opdager det før manifestet er lagt i kø.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from triage_review import classify, fold  # noqa: E402


def tier(title: str, authority: str = "", document_type: str = "") -> str:
    return classify(title, authority, document_type).tier


# ---------------------------------------------------------------------------
# Foldning
# ---------------------------------------------------------------------------


def test_foldning_matcher_projektets_konvention():
    assert fold("Søfartsstyrelsen") == "soefartsstyrelsen"
    assert fold("Ændringsbekendtgørelse") == "aendringsbekendtgoerelse"
    assert fold("HAVMILJØ") == "havmiljoe"


# ---------------------------------------------------------------------------
# Maritim kerne
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Lov om sikkerhedsundersøgelse af ulykker til søs",
        "Bekendtgørelse om sikring af havne og havnefaciliteter",
        "Cirkulære om Skibsfartens og Luftfartens Redningsråd",
        "Bekendtgørelse om andre landes tiltrædelse m.v. af konventionen af "
        "23. september 1910 om hjælp og bjærgning til søs",
        "Bekendtgørelse om klassifikation og kategorisering samt udtømning af "
        "flydende stoffer, der transporteres i bulk",
        "Vejledning vedrørende bekendtgørelse om modtageordninger for rester og "
        "blandinger af olie, kloakspildevand samt affald i danske havne",
        "Bekendtgørelse om Polens tiltrædelse af konvention om fiskeriet "
        "(den europæiske fiskerikonvention) af 9. marts 1964.",
    ],
)
def test_klassisk_maritimt_bliver_kerne(title):
    assert tier(title) == "core"


def test_fartoejskrav_slaar_fiskeriregulering():
    """Bekendtgørelsen handler om selve fartøjet, ikke om kvoten."""
    assert tier("Bekendtgørelse om fartøjer, der anvendes til erhvervsmæssigt "
                "fiskeri i saltvand") == "core"


# ---------------------------------------------------------------------------
# Fiskeri: støtteordning skal slå generel fiskeriregulering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Bekendtgørelse om tilskud til fiskerikontrol",
        "Bekendtgørelse om tilskudsordning for yngre fiskeres førstegangsetablering",
        "Bekendtgørelse om de minimis-støtte til kompensation for erhvervsmæssigt "
        "fiskeri efter ål",
        "Bekendtgørelse om støtte til ophugning af fiskerfartøjer for torskefiskere "
        "i Østersøen",
        "Lov om Hav-, Fiskeri- og Akvakulturfonden",
        "Bekendtgørelse om tilskud til investeringer på fiskerihavne og landingssteder",
    ],
)
def test_stoetteordninger_faar_egen_kategori(title):
    assert tier(title) == "support"


@pytest.mark.parametrize(
    "title",
    [
        "Straksregulering nr. 21 - 2023 Stop for fiskeri af tobis på rationsvilkår "
        "i tobisforvaltningsområde 2r i Nordsøen og Skagerrak",
        "Bekendtgørelse om regulering af fiskeriet i 2014-2020",
        "Bekendtgørelse om Fiskerikontrol (myndighed)",
    ],
)
def test_konkret_fiskeriregulering(title):
    assert tier(title) == "fishery"


def test_tilskud_til_fiskerikontrol_er_ikke_det_samme_som_fiskerikontrol():
    """Den ene er forvaltning af et tilskud, den anden er kontrolhjemlen."""
    assert tier("Bekendtgørelse om tilskud til fiskerikontrol") == "support"
    assert tier("Bekendtgørelse om Fiskerikontrol (myndighed)") == "fishery"


# ---------------------------------------------------------------------------
# Udelukkelser
# ---------------------------------------------------------------------------


def test_stednavne_goer_ikke_et_dokument_maritimt():
    assert tier(
        "Lov om ændring af lov om en Cityring og lov om Metroselskabet I/S og "
        "Udviklingsselskabet By & Havn I/S (Afgrening fra Cityringen til Sydhavnen, "
        "mulighed for udvidelse af afgreningen til Nordhavnen ...)"
    ) == "exclude"


def test_generel_svovlregulering_udelukkes():
    assert tier(
        "Bekendtgørelse om begrænsning af svovlindhold i brændsel til fyrings- "
        "og transportformål"
    ) == "exclude"


def test_svovl_i_skibsbraendstof_udelukkes_ikke():
    """Undtagelsesreglen skal forhindre, at SECA-regulering ryger ud."""
    assert tier("Bekendtgørelse om svovlindholdet i marine brændstoffer") != "exclude"


# ---------------------------------------------------------------------------
# Uafklaret
# ---------------------------------------------------------------------------


def test_administrativ_ophaevelse_kraever_menneske():
    assert tier(
        "Bekendtgørelse om ophævelse af cirkulære og visse meddelelser på "
        "Ministeriet for Fødevarer, Landbrug og Fiskeris område"
    ) == "housekeeping"


def test_ukendt_emne_forbliver_uafklaret():
    assert tier("Bekendtgørelse om kommunale biblioteker") == "unresolved"

"""Relevansvurdering af de titler, gennemgangen af review-bunken afdækkede.

Testene er skrevet mod den konfiguration, motoren indlæser som standard
(``config/maritime_keywords.yaml``). De beskriver derfor den ØNSKEDE tilstand
EFTER at ``config/maritime_keywords.additions.yaml`` er flettet ind::

    python3 scripts/merge_keyword_additions.py --out config/maritime_keywords.after.yaml
    # mål virkningen, og gør derefter after-filen til produktionsfilen

Før fletningen er de røde. Det er meningen: de er kravet, ikke kvitteringen.

Hvorfor et lokalt dokumentobjekt
--------------------------------
Motoren læser fire ting: ``title``, ``authority``, ``content`` og
``metadata_text()``. Testene konstruerer et minimalt objekt med netop dem i
stedet for en ``NormalizedDocument``, fordi forhåndsvurderingen i
``global_service.py`` også kun har titel og myndighed til rådighed — ingen
brødtekst. Det er nøjagtigt det grundlag, prescoren i
``manifests/discovery-global.csv`` blev beregnet på.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.services.relevance.keyword_engine import KeywordRelevanceEngine


@dataclass
class TitleOnlyDocument:
    """Titel + myndighed — samme grundlag som discover-globals forhåndsvurdering."""

    title: str
    authority: str = ""
    content: str = ""
    document_type: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def metadata_text(self) -> str:
        parts = [self.document_type, *(str(value) for value in self.metadata.values())]
        return " ".join(part for part in parts if part)


@pytest.fixture(scope="module")
def engine() -> KeywordRelevanceEngine:
    return KeywordRelevanceEngine()


def classify(engine: KeywordRelevanceEngine, title: str, authority: str = ""):
    return engine.classify(TitleOnlyDocument(title=title, authority=authority))


# ---------------------------------------------------------------------------
# De seks review-titler, der løses med vægte alene
# ---------------------------------------------------------------------------
# Hver lå mellem 32 og 46 i prescore og endte som `review`, selv om de er
# utvetydigt maritime. Rettelsen er en ordret titelterm på 9.0, som udløser
# titelgulvet (decisive_title_min_weight = 8.0 -> score 70).

REVIEW_TITLER = [
    pytest.param(
        "Lov om ændring af tonnageskatteloven, kildeskatteloven og "
        "selskabsskatteloven (Lejeindtægt m.v. af lokaler om bord, fast "
        "driftssted om bord)",
        "SKAT",
        "tonnageskat",
        id="A20040046030-tonnageskat",
    ),
    pytest.param(
        "Bekendtgørelse om klassifikation og kategorisering samt udtømning af "
        "flydende stoffer, der transporteres i bulk (udtømningsbekendtgørelsen)",
        "Miljøstyrelsen",
        "flydende stoffer, der transporteres i bulk",
        id="B19870016605-bulk",
    ),
    pytest.param(
        "Bekendtgørelse om tilskudsordning for yngre fiskeres førstegangsetablering",
        "Fiskeristyrelsen",
        "yngre fiskeres førstegangsetablering",
        id="B20170089805-foerstegangsetablering",
    ),
    pytest.param(
        "Bekendtgørelse om tilskudsordning for yngre fiskeres førstegangsetablering",
        "Fiskeristyrelsen",
        "yngre fiskeres førstegangsetablering",
        id="B20200117305-foerstegangsetablering",
    ),
    pytest.param(
        "Bekendtgørelse om tilskud til yngre fiskeres førstegangsetablering",
        "Fiskeristyrelsen",
        "yngre fiskeres førstegangsetablering",
        id="B20240029905-foerstegangsetablering",
    ),
    pytest.param(
        "Vejledning vedrørende bekendtgørelse om modtageordninger for rester og "
        "blandinger af olie, kloakspildevand samt affald i danske havne "
        "(Til havnebestyrelser, kommunalbestyrelser, amtsråd samt hovedstadsrådet)",
        "Miljøstyrelsen",
        "modtageordninger for rester",
        id="C19810006960-modtageordninger",
    ),
]


@pytest.mark.parametrize("title,authority,expected_term", REVIEW_TITLER)
def test_review_titel_bliver_maritim(engine, title, authority, expected_term):
    result = classify(engine, title, authority)
    assert result.is_maritime is True, f"score={result.score} termer={result.matched_terms}"
    assert result.score >= engine.maritime_threshold


@pytest.mark.parametrize("title,authority,expected_term", REVIEW_TITLER)
def test_review_titel_rammer_den_tilsigtede_term(engine, title, authority, expected_term):
    """Den skal blive maritim af den RIGTIGE grund, ikke ved et tilfælde."""
    result = classify(engine, title, authority)
    assert expected_term in result.matched_terms


@pytest.mark.parametrize("title,authority,expected_term", REVIEW_TITLER)
def test_titelgulvet_er_det_der_afgoer_sagen(engine, title, authority, expected_term):
    result = classify(engine, title, authority)
    assert result.title_floor_applied is True
    assert expected_term in result.title_floor_terms


# ---------------------------------------------------------------------------
# Redningsråd-cirkulærerne — bevidst uløste
# ---------------------------------------------------------------------------
# C20001137609 og C20020906409. Titlen er en bevidst blandet søfarts- og
# luftfartstitel, og `luftfart` i negative_terms slår titelgulvet fra
# (`title_has_negative` i keyword_engine.py) uanset vægt.
#
# BESLUTNING: motorlogikken ændres ikke for to kendte dokumenters skyld.
# Ingen `title_floor_exceptions`. De to fanges i stedet af
# scripts/triage_review.py (regel `skib`), som sætter dem til include.
#
# Opdelingen nedenfor er bevidst: kun selve klassifikationen er xfail.
# Ligger xfail'en på parametrene i stedet, arver ENHVER test, der bruger dem,
# markeringen — og en test, der blot kontrollerer at termen matcher, ville
# bestå og dermed give XPASS(strict=True) og gøre suiten rød.

REDNINGSRAAD_TITLER = [
    pytest.param(
        "Cirkulære om Skibsfartens og Luftfartens Redningsråd",
        "Forsvarsministeriet",
        id="C20001137609-redningsraad",
    ),
    pytest.param(
        "Cirkulære om Skibsfartens og Luftfartens Redningsråd",
        "Forsvarsministeriet",
        id="C20020906409-redningsraad",
    ),
]


@pytest.mark.parametrize("title,authority", REDNINGSRAAD_TITLER)
def test_redningsraad_termen_matcher(engine, title, authority):
    """Termen skal ramme — også selv om den ikke afgør klassifikationen."""
    result = classify(engine, title, authority)
    assert "Skibsfartens og Luftfartens Redningsråd" in result.matched_terms


@pytest.mark.parametrize("title,authority", REDNINGSRAAD_TITLER)
def test_redningsraad_titelgulvet_blokeres_af_luftfart(engine, title, authority):
    """Den dokumenterede årsag: en negativ term i titlen slår gulvet fra."""
    result = classify(engine, title, authority)
    assert result.title_floor_applied is False
    assert any(match.field == "title" for match in result.negative_matches)
    assert "luftfart" in {match.term for match in result.negative_matches}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Bevidst uløst. Titlen er blandet søfart/luftfart, og title_has_negative "
        "slår titelgulvet fra uanset vægt. Motorlogikken ændres ikke for to "
        "dokumenter; de håndteres af scripts/triage_review.py. Slår denne test om "
        "til XPASS, er gulvreglen ændret — tag stilling påny."
    ),
)
@pytest.mark.parametrize("title,authority", REDNINGSRAAD_TITLER)
def test_redningsraad_titel_bliver_maritim(engine, title, authority):
    result = classify(engine, title, authority)
    assert result.is_maritime is True


# ---------------------------------------------------------------------------
# Fiskerikontrol: den præcise titel afgør, det brede ord gør ikke
# ---------------------------------------------------------------------------


def test_bekendtgoerelse_om_fiskerikontrol_er_maritim(engine):
    result = classify(engine, "Bekendtgørelse om Fiskerikontrol (myndighed)", "Fiskeristyrelsen")
    assert result.is_maritime is True
    assert "bekendtgørelse om fiskerikontrol" in result.matched_terms


def test_fiskerikontrol_alene_udloeser_ikke_titelgulvet(engine):
    """Vægten 7.0 ligger bevidst under decisive_title_min_weight (8.0)."""
    result = classify(engine, "Bekendtgørelse om tilskud til fiskerikontrol", "Fiskeristyrelsen")
    assert result.title_floor_applied is False


def test_den_brede_modtageordning_udloeser_ikke_titelgulvet(engine):
    """'modtageordning' på 6.0 må ikke gøre enhver kommunal ordning maritim."""
    result = classify(engine, "Bekendtgørelse om kommunale modtageordninger for dagrenovation")
    assert result.title_floor_applied is False
    assert result.is_maritime is False


def test_de_praecise_titeltermer_ligger_over_gulvgraensen(engine):
    """Sikrer at vægtene ikke stille falder under 8.0 ved en senere redigering."""
    praecise = {
        "tonnageskat",
        "flydende stoffer, der transporteres i bulk",
        "yngre fiskeres førstegangsetablering",
        "modtageordninger for rester",
        "bekendtgørelse om fiskerikontrol",
        "Skibsfartens og Luftfartens Redningsråd",
    }
    vaegte = {spec.term: spec.weight for spec in engine.terms}
    for term in praecise:
        assert term in vaegte, f"termen mangler i konfigurationen: {term}"
        assert vaegte[term] >= engine.decisive_title_min_weight

    for bred in ("fiskerikontrol", "modtageordning"):
        assert vaegte[bred] < engine.decisive_title_min_weight


# ---------------------------------------------------------------------------
# Aktindsigtstitler — må ikke blive maritime
# ---------------------------------------------------------------------------
# Ombudsmandsudtalelser om aktindsigt. Emnet er forvaltningsret, ikke søfart:
# "Fiskerikontrollen" optræder alene som den myndighed, sagen angår. Det er
# præcis dét, sænkningen af `fiskerikontrol` til 7.0 skal fange — ordet skal
# give vægt, ikke afgøre sagen.
#
# Den første er oplyst registreret som FOU nr. 1987.54, Ministerium: Folketinget.

AKTINDSIGTSTITLER: list[tuple[str, str]] = [
    (
        "Aktindsigt i konsulentrapport om Fiskerikontrollen",
        "Folketinget",
    ),
    (
        "Aktindsigt i dokumenter vedrørende Fiskerikontrollen - sagsbehandlingstid m.m.",
        "Folketinget",
    ),
]


@pytest.mark.parametrize("title,authority", AKTINDSIGTSTITLER)
def test_aktindsigtstitel_bliver_ikke_maritim(engine, title, authority):
    result = classify(engine, title, authority)
    assert result.is_maritime is False, (
        f"score={result.score} termer={result.matched_terms} "
        f"titelgulv={result.title_floor_applied}"
    )
    assert result.title_floor_applied is False


@pytest.mark.parametrize("title,authority", AKTINDSIGTSTITLER)
def test_aktindsigtstitel_rammer_det_brede_ord_uden_at_afgoere_sagen(engine, title, authority):
    """Termen skal ramme — den må bare ikke være nok i sig selv."""
    result = classify(engine, title, authority)
    assert "fiskerikontrol" in result.matched_terms
    assert "bekendtgørelse om fiskerikontrol" not in result.matched_terms

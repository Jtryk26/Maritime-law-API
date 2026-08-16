"""Test af den maritime relevansmotor."""

from __future__ import annotations

import pytest

from tests.conftest import make_document
from tests.conftest import FIXTURE_NON_MARITIME, FIXTURE_STORED


# ---------------------------------------------------------------------------
# Kravene fra opgavespecifikationen
# ---------------------------------------------------------------------------


def test_passagerskibe_titel_er_maritim(relevance_engine):
    """Specifikationens hovedeksempel skal give høj maritim relevans."""
    result = relevance_engine.classify(
        make_document("Bekendtgørelse om sikkerhed på passagerskibe")
    )
    assert result.is_maritime is True
    assert result.classification == "maritime"
    assert result.score >= 60
    assert "passagerskib" in result.matched_terms


def test_folkeskole_titel_er_ikke_maritim(relevance_engine):
    """Klart urelateret lovgivning skal have meget lav score."""
    result = relevance_engine.classify(
        make_document("Bekendtgørelse om folkeskolens undervisning")
    )
    assert result.is_maritime is False
    assert result.score < 30
    assert result.classification == "not_maritime"


# ---------------------------------------------------------------------------
# Scoringsmodellen
# ---------------------------------------------------------------------------


def test_score_er_altid_i_intervallet_0_100(relevance_engine, fixture_client):
    for ref in fixture_client.get_documents():
        result = relevance_engine.classify(fixture_client.get_document(ref.source_id))
        assert 0 <= result.score <= 100


def test_gentagelse_af_samme_ord_giver_ikke_maksimal_score(relevance_engine):
    """Anti-spam: 500 gentagelser må ikke give topscore."""
    spam = relevance_engine.classify(make_document("Notat", "skib " * 500))
    assert spam.score < 60


def test_bredde_slaar_gentagelse(relevance_engine):
    """Flere uafhængige begreber er et stærkere signal end ét gentaget ord."""
    spam = relevance_engine.classify(make_document("Notat", "skib " * 500))
    bredde = relevance_engine.classify(
        make_document("Notat", "skib besætning havmiljø rederi lods navigation SOLAS")
    )
    assert bredde.score > spam.score
    assert len(bredde.concepts) > len(spam.concepts)


def test_forekomster_begraenses_af_loft(relevance_engine):
    result = relevance_engine.classify(make_document("Notat", "skib " * 50))
    skib = next(m for m in result.matches if m.term == "skib")
    assert skib.occurrences == 50
    assert skib.counted_occurrences == relevance_engine.max_occurrences


def test_titel_vejer_tungere_end_broedtekst(relevance_engine):
    i_titel = relevance_engine.classify(make_document("Regler om søfarende", ""))
    i_tekst = relevance_engine.classify(make_document("Regler", "søfarende"))
    assert i_titel.score > i_tekst.score


# ---------------------------------------------------------------------------
# Falske positiver og negative signaler
# ---------------------------------------------------------------------------


def test_lodsejer_matcher_ikke_lods(relevance_engine):
    """'lodsejer' er en grundejer og har intet med lodsning at gøre."""
    result = relevance_engine.classify(
        make_document(
            "Bekendtgørelse om lodsejeres pligter ved vandløb",
            "Lodsejeren skal vedligeholde vandløbet. Grundejeren afholder udgiften.",
        )
    )
    assert "lods" not in result.matched_terms
    assert result.is_maritime is False


def test_luftfart_daemper_scoren(relevance_engine):
    med_luftfart = relevance_engine.classify(
        make_document(
            "Bekendtgørelse om vedligeholdelse af luftfartøjer",
            "Flyvebesætningen underrettes om mangler ved luftfartøjet. "
            "Certifikatet fornys hvert femte år.",
        )
    )
    assert med_luftfart.is_maritime is False
    assert med_luftfart.negative_matches


def test_titelgulv_anvendes_ikke_ved_negativ_term_i_titel(relevance_engine):
    """En tvetydig titel må ikke løftes af titelautoritetsreglen."""
    result = relevance_engine.classify(
        make_document("Bekendtgørelse om skibsfart og luftfart", "Regler for begge områder.")
    )
    assert result.title_floor_applied is False


def test_maritimt_dokument_overdaempes_ikke_af_enkelt_luftfartsreference(relevance_engine):
    result = relevance_engine.classify(
        make_document(
            "Bekendtgørelse om transport af farligt gods med skib",
            "Reglerne gælder søtransport. Ved omladning til luftfartøj gælder "
            "luftfartslovgivningen. Skibsføreren sikrer korrekt stuvning om bord.",
            authority="Søfartsstyrelsen",
        )
    )
    assert result.is_maritime is True


# ---------------------------------------------------------------------------
# Gennemsigtighed
# ---------------------------------------------------------------------------


def test_resultatet_forklarer_sig_selv(relevance_engine):
    result = relevance_engine.classify(
        make_document(
            "Bekendtgørelse om redningsmidler i handelsskibe",
            "Redningsflåder efterses årligt. Søfartsstyrelsen fører tilsyn.",
            authority="Søfartsstyrelsen",
        )
    )
    assert result.reason
    assert result.matched_terms
    assert result.engine == "keyword"
    payload = result.to_json()
    assert payload["calculation"]["field_contributions"]
    assert payload["matches"][0]["contribution"] > 0


def test_regnestykket_kan_efterproeves(relevance_engine):
    """Rå score skal svare til summen af bidrag plus bonus minus negative."""
    result = relevance_engine.classify(
        make_document(
            "Bekendtgørelse om skibssikkerhed",
            "Skibet skal være sødygtigt. Besætningen instrueres. Havmiljøet beskyttes.",
            authority="Søfartsstyrelsen",
        )
    )
    sum_positive = sum(m.contribution for m in result.matches)
    sum_negative = sum(m.contribution for m in result.negative_matches)
    forventet = max(0.0, sum_positive + result.concept_bonus - sum_negative)
    assert result.raw_score == pytest.approx(forventet)
    assert result.positive_raw == pytest.approx(sum_positive)


def test_ingen_maritime_termer_giver_score_nul(relevance_engine):
    result = relevance_engine.classify(
        make_document("Bekendtgørelse om kommunale biblioteker", "Biblioteket er åbent.")
    )
    assert result.score == 0
    assert result.matched_terms == []
    assert "Ingen maritime termer" in result.reason


def test_taerskler_returneres_med_resultatet(relevance_engine):
    result = relevance_engine.classify(make_document("Test"))
    assert result.thresholds["maritime"] == relevance_engine.maritime_threshold
    assert result.thresholds["possible"] == relevance_engine.possible_threshold


# ---------------------------------------------------------------------------
# Fixtursættet som helhed
# ---------------------------------------------------------------------------


def test_fixtures_adskiller_maritimt_fra_ikke_maritimt(relevance_engine, fixture_client):
    """De maritime fixturer skal klassificeres maritimt, de øvrige ikke."""
    maritime, ikke_maritime = [], []
    for ref in fixture_client.get_documents():
        document = fixture_client.get_document(ref.source_id)
        result = relevance_engine.classify(document)
        (maritime if result.is_maritime else ikke_maritime).append(document.title)

    assert len(maritime) == FIXTURE_STORED
    assert len(ikke_maritime) == FIXTURE_NON_MARITIME
    assert all(
        any(ord_ in titel.lower() for ord_ in ("folkeskole", "dagtilbud", "luftfartøj"))
        for titel in ikke_maritime
    )

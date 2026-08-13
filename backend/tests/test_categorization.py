"""Test af den maritime kategorisering."""

from __future__ import annotations

from tests.conftest import make_document


def test_alle_kategorier_fra_konfigurationen_er_defineret(categorization_engine):
    slugs = {d.slug for d in categorization_engine.definitions()}
    forventede = {
        "skibssikkerhed", "brandsikkerhed", "redningsmidler-lsa", "maskineri",
        "elektriske-installationer", "miljo-marpol", "besaetning", "uddannelse-stcw",
        "arbejdsforhold", "navigation", "radio-kommunikation", "ism-sikkerhedsledelse",
        "isps-sikring", "passagerskibe", "lastskibe", "fiskeskibe", "havne",
        "forurening", "certifikater-og-syn", "soeulykker", "rederiets-ansvar",
        "generel-soefartslovgivning", "andet-maritimt",
    }
    assert forventede <= slugs


def test_brandsikkerhed_i_passagerskibe_faar_begge_kategorier(categorization_engine):
    """Specifikationens eksempel: dokumentet hører til flere kategorier."""
    result = categorization_engine.categorize(
        make_document(
            "Bekendtgørelse om brandsikkerhed i passagerskibe",
            "Skibet inddeles i brandsektioner. Brandalarmanlægget dækker alle "
            "opholdsrum. Der afholdes brandøvelse hver 14. dag for passagerernes "
            "sikkerhed.",
        )
    )
    assert "brandsikkerhed" in result.slugs
    assert "passagerskibe" in result.slugs


def test_marpol_dokument_kategoriseres_som_miljoe(categorization_engine):
    result = categorization_engine.categorize(
        make_document(
            "Bekendtgørelse om forebyggelse af forurening fra skibe",
            "MARPOL-konventionen. Udtømning af olie er forbudt. Svovlindholdet "
            "i brændolie begrænses. Havmiljøet beskyttes.",
        )
    )
    assert result.assignments[0].slug == "miljo-marpol"


def test_stcw_dokument_kategoriseres_som_uddannelse(categorization_engine):
    result = categorization_engine.categorize(
        make_document(
            "Bekendtgørelse om uddannelse og beviser for søfarende",
            "STCW-konventionen. Sønæringsbevis udstedes efter gennemført maritim "
            "uddannelse og bestået eksamen. Duelighedsbevis kræves.",
        )
    )
    assert "uddannelse-stcw" in result.slugs


def test_konfidens_ligger_i_gyldigt_interval(categorization_engine, fixture_client):
    for ref in fixture_client.get_documents():
        result = categorization_engine.categorize(fixture_client.get_document(ref.source_id))
        for assignment in result.assignments:
            assert 0.0 <= assignment.confidence <= 1.0


def test_antal_kategorier_begraenses(categorization_engine, fixture_client):
    for ref in fixture_client.get_documents():
        result = categorization_engine.categorize(fixture_client.get_document(ref.source_id))
        assert len(result.assignments) <= categorization_engine.max_categories


def test_kategorier_sorteres_med_staerkeste_foerst(categorization_engine):
    result = categorization_engine.categorize(
        make_document(
            "Bekendtgørelse om redningsmidler i handelsskibe",
            "Redningsflåder, redningsbåde og redningsveste efterses årligt. "
            "LSA-koden gennemføres.",
        )
    )
    scores = [a.raw_score for a in result.assignments]
    assert scores == sorted(scores, reverse=True)
    assert result.assignments[0].slug == "redningsmidler-lsa"


def test_maritimt_dokument_uden_kategorimatch_faar_fallback(categorization_engine):
    """Et maritimt dokument må aldrig ende helt ukategoriseret."""
    result = categorization_engine.categorize(
        make_document("Bekendtgørelse om søfart", "Kort tekst uden fagtermer.")
    )
    assert result.assignments
    assert result.assignments[0].slug == "andet-maritimt"
    assert result.assignments[0].is_fallback is True


def test_tildeling_angiver_matchede_termer(categorization_engine):
    result = categorization_engine.categorize(
        make_document(
            "Bekendtgørelse om sikringsniveauer i havne",
            "ISPS-koden. Sikringsplanen beskriver adgangskontrol. "
            "Sikringsofficeren udpeges.",
        )
    )
    isps = next(a for a in result.assignments if a.slug == "isps-sikring")
    assert isps.matched_terms

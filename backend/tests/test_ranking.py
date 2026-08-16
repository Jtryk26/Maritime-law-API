"""Domænejusteret rangering: klassifikation, query intent og score.

De tre scenarier fra specifikationen er de vigtigste tests i filen:

    "hviletid"                     -> hovedreglen først
    "fiskeskib hviletid"           -> fiskeskibsreglen først
    "grønlandske lodser hviletid"  -> den grønlandske lodsregel først

Det er den samme forespørgsel om det samme emne tre gange. Kun ordvalget
ændrer sig, og alligevel skal svaret være tre forskellige dokumenter.
Kan systemet det, virker hele kæden: klassifikation af dokumentet,
klassifikation af søgningen og domænereglerne imellem dem.
"""

from __future__ import annotations

import pytest

from app.services.categorization import KeywordCategorizationEngine
from app.services.importer import ImportService
from app.services.ranking import (
    DomainRanker,
    LawClass,
    LawClassifier,
    RankingSignals,
    classify_query_intent,
    refine_intent,
)
from app.services.relevance import KeywordRelevanceEngine
from app.services.retsinformation import FixtureRetsinformationClient
from app.services.search import SearchQuery, get_search_backend


# ---------------------------------------------------------------------------
# Klassifikation af dokumenter
# ---------------------------------------------------------------------------


@pytest.fixture()
def classifier():
    return LawClassifier()


class TestLawClass:
    def test_bred_bekendtgoerelse_er_kernelov(self, classifier):
        result = classifier.classify(
            title="Bekendtgørelse om redningsmidler i handelsskibe",
            document_type="Bekendtgørelse",
            authority="Søfartsstyrelsen",
            status="Gældende",
            maritime_score=90,
        )
        assert result.law_class == LawClass.CORE
        assert result.niche_groups == []

    def test_lov_om_sikkerhed_til_soes_er_kernelov(self, classifier):
        result = classifier.classify(
            title="Bekendtgørelse af lov om sikkerhed til søs",
            document_type="Lovbekendtgørelse",
            authority="Erhvervsministeriet",
            status="Gældende",
            maritime_score=89,
        )
        assert result.law_class == LawClass.CORE
        assert result.authority_score > 0.9

    def test_fiskeskibsregel_er_speciallov(self, classifier):
        result = classifier.classify(
            title="Bekendtgørelse om arbejdstid og hviletid på fiskeskibe",
            document_type="Bekendtgørelse",
            authority="Søfartsstyrelsen",
            status="Gældende",
            maritime_score=86,
        )
        assert result.law_class == LawClass.SPECIAL
        assert "fiskeskibe" in result.niche_groups

    def test_flere_nichemarkoerer_giver_smallere_anvendelse(self, classifier):
        bred = classifier.classify(
            title="Bekendtgørelse om hviletid for søfarende",
            document_type="Bekendtgørelse",
            authority="Søfartsstyrelsen",
            maritime_score=85,
        )
        smal = classifier.classify(
            title="Bekendtgørelse om hviletid for lodser i grønlandske farvande",
            document_type="Bekendtgørelse",
            authority="Søfartsstyrelsen",
            maritime_score=85,
        )
        assert set(smal.niche_groups) == {"groenland", "lodseri"}
        assert smal.scope_score < bred.scope_score

    def test_vejledning_er_stoettedokument(self, classifier):
        result = classifier.classify(
            title="Vejledning om hviletid for søfarende",
            document_type="Vejledning",
            authority="Søfartsstyrelsen",
            status="Gældende",
            maritime_score=80,
        )
        assert result.law_class == LawClass.SUPPORT

    def test_aendringsbekendtgoerelse_er_stoettedokument(self, classifier):
        result = classifier.classify(
            title="Bekendtgørelse om ændring af bekendtgørelse om brandsikkerhed",
            document_type="Bekendtgørelse",
            authority="Søfartsstyrelsen",
            status="Gældende",
            maritime_score=88,
        )
        assert result.law_class == LawClass.SUPPORT

    def test_historisk_status_aendrer_ikke_dokumentets_rolle(self, classifier):
        """Status og rolle er to akser.

        En ophævet særregel om fiskeskibe er stadig en særregel om
        fiskeskibe. Blev den omklassificeret til støttedokument, ville den
        miste sin nichemarkering og ikke længere kunne findes ved en
        nichesøgning — og nedjusteringen ville blive talt to gange.
        """
        result = classifier.classify(
            title="Bekendtgørelse om sikkerhed på fiskeskibe",
            document_type="Bekendtgørelse",
            authority="Søfartsstyrelsen",
            status="Ophævet",
            maritime_score=85,
        )
        assert result.law_class == LawClass.SPECIAL
        assert "fiskeskibe" in result.niche_groups

    def test_klassifikationen_begrundes(self, classifier):
        result = classifier.classify(
            title="Bekendtgørelse om fritidsfartøjers udrustning",
            document_type="Bekendtgørelse",
            authority="Søfartsstyrelsen",
            maritime_score=70,
        )
        assert result.reasons
        assert any("Fritidsfartøjer" in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# Klassifikation af søgninger
# ---------------------------------------------------------------------------


class TestQueryIntent:
    @pytest.mark.parametrize(
        ("query", "kind"),
        [
            ("hviletid", "broad"),
            ("brand passagerskib", "broad"),
            ("fiskeskib hviletid", "niche"),
            ("grønlandske lodser hviletid", "niche"),
            ("hviletid for søfarende om bord på danske skibe", "semi"),
        ],
    )
    def test_typer(self, query, kind):
        assert classify_query_intent(query).kind == kind

    def test_nichegrupper_navngives(self):
        intent = classify_query_intent("grønlandske lodser hviletid")
        assert set(intent.niche_groups) == {"groenland", "lodseri"}

    def test_tom_soegning_er_bred(self):
        assert classify_query_intent("").kind == "broad"

    def test_sjaelden_term_goer_soegningen_specifik(self):
        """Ordvalget alene afgør ikke, om en søgning er bred.

        "trawlspil" er ét ord uden nichemarkør og læses derfor først som
        bred. Findes ordet kun i ét dokument, er søgningen i praksis så
        specifik, den kan blive — og de brede domæneregler ville ellers
        nedjustere netop det dokument, brugeren ledte efter, under
        dokumenter der slet ikke indeholder ordet.
        """
        intent = classify_query_intent("trawlspil")
        assert intent.kind == "broad"

        refined = refine_intent(intent, lexical_result_count=1)
        assert refined.kind == "niche"
        assert refined.refined_from == "broad"
        assert refined.refinement_reason

    def test_mange_traef_efterlader_soegningen_bred(self):
        intent = classify_query_intent("hviletid")
        assert refine_intent(intent, lexical_result_count=40).kind == "broad"


# ---------------------------------------------------------------------------
# Selve scoren
# ---------------------------------------------------------------------------


def _signals(**kwargs) -> RankingSignals:
    base = {
        "document_id": 1,
        "lexical_position": 1,
        "law_class": LawClass.CORE,
        "scope_score": 0.75,
        "authority_score": 0.9,
        "maritime_score": 88,
        "status": "Gældende",
    }
    base.update(kwargs)
    return RankingSignals(**base)


class TestDomainRanker:
    @pytest.fixture()
    def ranker(self):
        return DomainRanker()

    def test_foerstepladsen_giver_fuld_leksikalsk_score(self, ranker):
        breakdown = ranker.score(_signals(), classify_query_intent("hviletid"))
        assert breakdown.lexical_score == pytest.approx(1.0)

    def test_daarligere_placering_giver_lavere_score(self, ranker):
        intent = classify_query_intent("hviletid")
        first = ranker.score(_signals(lexical_position=1), intent)
        tenth = ranker.score(_signals(lexical_position=10), intent)
        assert first.lexical_score > tenth.lexical_score > 0

    def test_kernelov_opjusteres_ved_bred_soegning(self, ranker):
        intent = classify_query_intent("hviletid")
        core = ranker.score(_signals(law_class=LawClass.CORE), intent)
        special = ranker.score(
            _signals(document_id=2, law_class=LawClass.SPECIAL, niche_groups=["fiskeskibe"]),
            intent,
        )
        assert core.multiplier > 1.0 > special.multiplier
        assert core.final_score > special.final_score

    def test_speciallov_opjusteres_naar_nichen_matcher(self, ranker):
        intent = classify_query_intent("fiskeskib hviletid")
        matching = ranker.score(
            _signals(law_class=LawClass.SPECIAL, niche_groups=["fiskeskibe"]), intent
        )
        assert matching.multiplier > 1.0
        assert any(a.name == "matching_speciallaw_boost" for a in matching.adjustments)

    def test_anden_niche_opjusteres_ikke(self, ranker):
        intent = classify_query_intent("fiskeskib hviletid")
        other = ranker.score(
            _signals(law_class=LawClass.SPECIAL, niche_groups=["groenland"]), intent
        )
        assert other.multiplier < 1.0

    def test_historisk_nedjusteres(self, ranker):
        intent = classify_query_intent("hviletid")
        current = ranker.score(_signals(status="Gældende"), intent)
        historic = ranker.score(_signals(document_id=2, status="Historisk"), intent)

        assert historic.final_score < current.final_score
        assert any(a.name == "historic_penalty" for a in historic.adjustments)

    def test_ingen_regel_kan_nulstille_et_resultat(self, ranker):
        """Et dokument der matcher, skal kunne findes — bare længere nede."""
        intent = classify_query_intent("hviletid")
        worst = ranker.score(
            _signals(law_class=LawClass.SUPPORT, status="Ophævet"), intent
        )
        assert worst.multiplier > 0.0
        assert worst.final_score > 0.0

    def test_hver_justering_kan_forklares(self, ranker):
        intent = classify_query_intent("hviletid")
        breakdown = ranker.score(_signals(law_class=LawClass.SUPPORT), intent)
        assert breakdown.adjustments
        assert all(a.reason for a in breakdown.adjustments)

    def test_regnestykket_hoenger_sammen(self, ranker):
        breakdown = ranker.score(_signals(), classify_query_intent("hviletid"))
        assert breakdown.final_score == pytest.approx(
            breakdown.base_score * breakdown.multiplier
        )


# ---------------------------------------------------------------------------
# Ende-til-ende: de tre scenarier fra specifikationen
# ---------------------------------------------------------------------------


@pytest.fixture()
def ranked(seeded_session):
    ImportService(
        seeded_session,
        client=FixtureRetsinformationClient(revision=1),
        relevance_engine=KeywordRelevanceEngine(),
        categorization_engine=KeywordCategorizationEngine(),
    ).run()
    return seeded_session


def _titles(session, query: str, mode: str = "lexical", **kwargs) -> list[str]:
    backend = get_search_backend(session, mode)
    results = backend.search(session, SearchQuery(q=query, mode=mode, page_size=10, **kwargs))
    return [hit.document.title for hit in results.hits]


class TestScenarier:
    def test_bred_soegning_giver_hovedreglen_foerst(self, ranked):
        titles = _titles(ranked, "hviletid")
        assert titles, "søgningen skal give resultater"
        assert titles[0] == (
            "Bekendtgørelse om besætningsfastsættelse og hviletid for søfarende"
        )

    def test_bred_soegning_lader_ikke_nichereglen_vinde(self, ranked):
        titles = _titles(ranked, "hviletid")
        groenlandsk = (
            "Bekendtgørelse om hviletid og tjenestetid for lodser i grønlandske farvande"
        )
        assert groenlandsk in titles, "nichereglen skal stadig kunne findes"
        assert titles.index(groenlandsk) > 0

    def test_gaeldende_staar_foer_historisk_version(self, ranked):
        """Samme titel, to statusser. Gældende ret skal stå først."""
        titles = _titles(ranked, "besætningsfastsættelse hviletid")
        assert titles[0] == (
            "Bekendtgørelse om besætningsfastsættelse og hviletid for søfarende"
        )

    def test_nichesoegning_paa_fiskeskibe(self, ranked):
        titles = _titles(ranked, "fiskeskib hviletid")
        assert titles[0] == "Bekendtgørelse om arbejdstid og hviletid på fiskeskibe"

    def test_nichesoegning_paa_groenlandske_lodser(self, ranked):
        titles = _titles(ranked, "grønlandske lodser hviletid")
        assert titles[0] == (
            "Bekendtgørelse om hviletid og tjenestetid for lodser i grønlandske farvande"
        )

    def test_stoettedokument_staar_efter_reglen_det_aendrer(self, ranked):
        titles = _titles(ranked, "brandsikkerhed passagerskibe")
        regel = "Bekendtgørelse om brandsikkerhed i passagerskibe"
        aendring = (
            "Bekendtgørelse om ændring af bekendtgørelse om brandsikkerhed i passagerskibe"
        )
        assert titles.index(regel) < titles.index(aendring)

    def test_resultatet_peger_paa_en_paragraf(self, ranked):
        backend = get_search_backend(ranked, "lexical")
        results = backend.search(ranked, SearchQuery(q="hviletid", mode="lexical"))
        top = results.hits[0]

        assert top.paragraph is not None
        assert top.paragraph.paragraph_id.startswith("§")
        assert top.paragraph.chapter_no
        assert "hviletid" in top.paragraph.snippet.lower()

    def test_filter_paa_dokumentklasse(self, ranked):
        backend = get_search_backend(ranked, "lexical")
        results = backend.search(
            ranked,
            SearchQuery(q="hviletid", mode="lexical", law_classes=["speciallaw"]),
        )
        assert results.hits
        assert all(hit.document.law_class == "speciallaw" for hit in results.hits)

    def test_uden_soegestreng_staar_kernelovene_foerst(self, ranked):
        """Standardlisten er en gennemsynsliste — og da skal de centrale
        love stå øverst, ikke det dokument der tilfældigvis er nyest."""
        backend = get_search_backend(ranked, "lexical")
        results = backend.search(ranked, SearchQuery(q=None, page_size=6))

        classes = [hit.document.law_class for hit in results.hits]
        assert classes[0] == LawClass.CORE
        # Ingen støttedokumenter i toppen af en ufiltreret liste.
        assert LawClass.SUPPORT not in classes[:3]

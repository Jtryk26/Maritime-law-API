"""Test af måling af søgekvalitet.

To slags test her, og de har hver sit formål:

* **Måletallene** kontrolleres mod eksempler regnet i hånden. Et måletal,
  ingen kan efterregne, er værre end intet måletal, fordi det bliver
  troet.
* **Kæden** — indlæsning, kørsel, gennemgangs-CSV, import — afprøves mod
  fixtursamlingen. Testene siger intet om, hvor god søgningen ER; de
  siger, at målingen af den virker.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.services.evaluation import (
    EvalQuery,
    EvalSet,
    EvalSetError,
    EvaluationRunner,
    first_relevant_rank,
    load_eval_set,
    mean,
    ndcg_at_k,
    precision_at_k,
    read_reviewed_csv,
    recall_at_k,
    reciprocal_rank,
    save_eval_set,
    scaffold_candidates,
    write_candidate_csv,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_EVAL_SET = REPO_ROOT / "data" / "eval" / "fixture-queries.yaml"


# ---------------------------------------------------------------------------
# Måletal
# ---------------------------------------------------------------------------


class TestMaaletal:
    def test_recall_er_andelen_af_facit_der_blev_fundet(self):
        assert recall_at_k(["a", "b", "c"], {"a", "d"}, 10) == pytest.approx(0.5)
        assert recall_at_k(["a", "d"], {"a", "d"}, 10) == pytest.approx(1.0)
        assert recall_at_k(["x", "y"], {"a"}, 10) == pytest.approx(0.0)

    def test_recall_respekterer_k(self):
        """Et træf på plads 5 tæller ikke, hvis brugeren kun ser tre."""
        assert recall_at_k(["x", "y", "z", "q", "a"], {"a"}, 3) == pytest.approx(0.0)
        assert recall_at_k(["x", "y", "z", "q", "a"], {"a"}, 5) == pytest.approx(1.0)

    def test_recall_uden_facit_er_nul_ikke_en_exception(self):
        assert recall_at_k(["a"], set(), 10) == 0.0

    def test_praecision_deler_med_k_ikke_med_antal_fundne(self):
        """Loftet er bevidst: med ét rigtigt svar kan P@10 aldrig overstige 0,1."""
        assert precision_at_k(["a", "x", "y"], {"a"}, 10) == pytest.approx(0.1)
        assert precision_at_k(["a", "b"], {"a", "b"}, 2) == pytest.approx(1.0)

    def test_foerste_traef_er_1_indekseret(self):
        assert first_relevant_rank(["x", "a", "b"], {"a"}) == 2
        assert first_relevant_rank(["x", "y"], {"a"}) is None

    def test_reciprok_rang(self):
        assert reciprocal_rank(["a"], {"a"}) == pytest.approx(1.0)
        assert reciprocal_rank(["x", "a"], {"a"}) == pytest.approx(0.5)
        assert reciprocal_rank(["x", "y", "z", "q", "w", "e", "r", "t", "y", "a"], {"a"}) == (
            pytest.approx(0.1)
        )
        assert reciprocal_rank(["x"], {"a"}) == 0.0

    def test_ndcg_belonner_hoej_placering(self):
        """Regnet i hånden: ét rigtigt dokument, IDCG = 1/log2(2) = 1."""
        assert ndcg_at_k(["a", "x", "y"], {"a"}, 10) == pytest.approx(1.0)
        # Plads 2: DCG = 1/log2(3) = 0,6309
        assert ndcg_at_k(["x", "a"], {"a"}, 10) == pytest.approx(1 / math.log2(3), abs=1e-6)
        # Plads 3: DCG = 1/log2(4) = 0,5
        assert ndcg_at_k(["x", "y", "a"], {"a"}, 10) == pytest.approx(0.5, abs=1e-6)

    def test_ndcg_er_1_naar_alt_rigtigt_ligger_oeverst(self):
        assert ndcg_at_k(["a", "b", "x"], {"a", "b"}, 10) == pytest.approx(1.0)

    def test_ndcg_adskiller_sig_fra_recall(self):
        """Samme recall, forskellig rækkefølge — det er hele pointen."""
        good = ["a", "x", "y", "z"]
        bad = ["x", "y", "z", "a"]
        assert recall_at_k(good, {"a"}, 10) == recall_at_k(bad, {"a"}, 10)
        assert ndcg_at_k(good, {"a"}, 10) > ndcg_at_k(bad, {"a"}, 10)

    def test_gennemsnit_af_tom_liste_er_nul(self):
        assert mean([]) == 0.0
        assert mean([1.0, 0.0]) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Evalueringssættets format
# ---------------------------------------------------------------------------


class TestEvalSet:
    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "eval.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_indlaesning(self, tmp_path):
        path = self._write(
            tmp_path,
            """
corpus: test
synthetic: true
queries:
  - query: livbåde
    intent: ordforrådskløft
    relevant: [DOC-1, DOC-2]
    tags: [ordforraad]
  - query: folkeskole
    relevant: []
""",
        )
        eval_set = load_eval_set(path)

        assert eval_set.corpus == "test"
        assert eval_set.synthetic is True
        assert len(eval_set.queries) == 2
        assert eval_set.queries[0].relevant == {"DOC-1", "DOC-2"}
        assert eval_set.queries[0].intent == "ordforrådskløft"
        assert eval_set.graded[0].query == "livbåde"
        assert eval_set.negative_controls[0].query == "folkeskole"
        assert eval_set.all_relevant_ids == {"DOC-1", "DOC-2"}

    def test_tom_relevant_er_negativ_kontrol(self):
        assert EvalQuery(query="x").is_negative_control is True
        assert EvalQuery(query="x", relevant={"A"}).is_negative_control is False

    def test_dublet_afvises(self, tmp_path):
        """To ens søgninger ville vægte netop det spørgsmål dobbelt."""
        path = self._write(
            tmp_path,
            """
queries:
  - query: skib
    relevant: [A]
  - query: Skib
    relevant: [B]
""",
        )
        with pytest.raises(EvalSetError, match="mere end én gang"):
            load_eval_set(path)

    def test_manglende_soegestreng_afvises(self, tmp_path):
        path = self._write(tmp_path, "queries:\n  - relevant: [A]\n")
        with pytest.raises(EvalSetError, match="mangler 'query'"):
            load_eval_set(path)

    def test_tomt_saet_afvises(self, tmp_path):
        path = self._write(tmp_path, "corpus: test\nqueries: []\n")
        with pytest.raises(EvalSetError, match="mangler eller er tom"):
            load_eval_set(path)

    def test_ukendt_fil_giver_forklarende_fejl(self, tmp_path):
        with pytest.raises(EvalSetError, match="findes ikke"):
            load_eval_set(tmp_path / "nope.yaml")

    def test_skrivning_og_indlaesning_er_symmetrisk(self, tmp_path):
        original = EvalSet(
            queries=[
                EvalQuery(query="livbåde", relevant={"B", "A"}, intent="hvorfor", tags=["t"]),
                EvalQuery(query="folkeskole"),
            ],
            corpus="test",
            synthetic=True,
            description="beskrivelse",
        )
        path = save_eval_set(original, tmp_path / "ud.yaml")
        reloaded = load_eval_set(path)

        assert reloaded.corpus == "test"
        assert reloaded.synthetic is True
        assert [q.query for q in reloaded.queries] == ["livbåde", "folkeskole"]
        assert reloaded.queries[0].relevant == {"A", "B"}


class TestFixtursaettet:
    """Værn om det medfølgende evalueringssæt."""

    def test_kan_indlaeses(self):
        eval_set = load_eval_set(FIXTURE_EVAL_SET)
        assert eval_set.corpus == "fixture"
        assert len(eval_set.graded) >= 15
        assert len(eval_set.negative_controls) >= 2

    def test_er_markeret_syntetisk(self):
        """Tal målt på 18 konstruerede dokumenter må aldrig fremstå som
        en udtalelse om den rigtige samling."""
        assert load_eval_set(FIXTURE_EVAL_SET).synthetic is True

    def test_facit_peger_kun_paa_fixturdokumenter(self):
        ids = load_eval_set(FIXTURE_EVAL_SET).all_relevant_ids
        assert ids
        assert all(i.startswith("FIXT-") for i in ids), sorted(ids)[:5]

    def test_daekker_baade_ordforraad_og_eksakte_termer(self):
        """Et sæt kun med eksakte termer ville gøre ordsøgning perfekt og
        betydningssøgning overflødig — og måle det forkerte."""
        tags = {tag for q in load_eval_set(FIXTURE_EVAL_SET).queries for tag in q.tags}
        assert {"ordforraad", "eksakt-term", "negativ-kontrol"} <= tags


# ---------------------------------------------------------------------------
# Kørsel
# ---------------------------------------------------------------------------


class TestKoersel:
    def test_koerer_alle_tilstande_mod_fixtursamlingen(self, indexed_session):
        eval_set = load_eval_set(FIXTURE_EVAL_SET)
        report = EvaluationRunner(indexed_session, k=10).run(
            eval_set, ["lexical", "semantic", "hybrid"]
        )

        assert [s.mode for s in report.summaries] == ["lexical", "semantic", "hybrid"]
        assert report.synthetic is True
        assert all(s.queries == len(eval_set.graded) for s in report.summaries)
        assert not report.missing_from_corpus, report.missing_from_corpus

    def test_hybrid_taber_ikke_recall_i_forhold_til_leksikalsk(self, indexed_session):
        """Det bærende krav ved sammensmeltningen: den må tilføje, ikke tage."""
        eval_set = load_eval_set(FIXTURE_EVAL_SET)
        report = EvaluationRunner(indexed_session, k=10).run(eval_set, ["lexical", "hybrid"])

        lexical, hybrid = report.summaries
        assert hybrid.recall >= lexical.recall

    def test_eksakte_termer_bevares_i_hybrid(self, indexed_session):
        """En paragrafhenvisning må ikke fortrænges af noget beslægtet."""
        eval_set = EvalSet(
            queries=[
                EvalQuery(query="MARPOL bilag VI", relevant={"FIXT-BEK-2024-0088"}),
                EvalQuery(query="trawlspil", relevant={"FIXT-BEK-2023-0644"}),
                EvalQuery(query="1290", relevant={"FIXT-BEK-2021-1290"}),
            ],
            corpus="fixture",
        )
        report = EvaluationRunner(indexed_session, k=10).run(eval_set, ["hybrid"])

        for outcome in report.summaries[0].outcomes:
            assert outcome.first_hit_rank == 1, (outcome.query, outcome.retrieved)

    def test_facit_der_peger_uden_for_samlingen_rapporteres(self, indexed_session):
        """Ellers ville man lede efter fejlen i søgemaskinen."""
        eval_set = EvalSet(
            queries=[EvalQuery(query="skib", relevant={"FINDES-IKKE-001"})], corpus="fixture"
        )
        report = EvaluationRunner(indexed_session, k=10).run(eval_set, ["lexical"])

        assert report.missing_from_corpus == ["FINDES-IKKE-001"]

    def test_negative_kontroller_taelles_for_sig(self, indexed_session):
        eval_set = EvalSet(
            queries=[
                EvalQuery(query="brand passagerskib", relevant={"FIXT-BEK-2023-0101"}),
                EvalQuery(query="folkeskolens undervisning"),
            ],
            corpus="fixture",
        )
        summary = EvaluationRunner(indexed_session, k=10).run(eval_set, ["lexical"]).summaries[0]

        # Den negative kontrol indgår ikke i recall-gennemsnittet.
        assert summary.queries == 1
        assert summary.negative_controls == 1
        assert summary.negative_controls_passed == 1
        assert summary.recall == pytest.approx(1.0)

    def test_nedgradering_markeres_i_rapporten(self, seeded_session):
        """Uden vektorer måler 'hybrid'-rækken i virkeligheden leksikalsk."""
        from app.services.categorization import KeywordCategorizationEngine
        from app.services.importer import ImportService
        from app.services.relevance import KeywordRelevanceEngine
        from app.services.retsinformation import FixtureRetsinformationClient

        ImportService(
            seeded_session,
            client=FixtureRetsinformationClient(revision=1),
            relevance_engine=KeywordRelevanceEngine(),
            categorization_engine=KeywordCategorizationEngine(),
        ).run()

        eval_set = EvalSet(
            queries=[EvalQuery(query="brand", relevant={"FIXT-BEK-2023-0101"})], corpus="fixture"
        )
        summary = EvaluationRunner(seeded_session, k=10).run(eval_set, ["hybrid"]).summaries[0]

        assert summary.downgraded is True
        assert summary.outcomes[0].effective_mode == "lexical"

    def test_rapport_kan_serialiseres(self, indexed_session):
        eval_set = EvalSet(
            queries=[EvalQuery(query="skib", relevant={"FIXT-BEK-2022-0450"})], corpus="fixture"
        )
        payload = EvaluationRunner(indexed_session, k=5).run(eval_set, ["lexical"]).to_json()

        assert payload["k"] == 5
        assert payload["modes"][0]["mode"] == "lexical"
        assert payload["per_query"][0]["query"] == "skib"


# ---------------------------------------------------------------------------
# Gennemgangs-CSV
# ---------------------------------------------------------------------------


class TestGennemgang:
    def test_kandidater_samles_fra_alle_tilstande(self, indexed_session):
        """Bygges facit kun af det ordsøgningen fandt, kan betydnings-
        søgningen aldrig vise sin værdi, og målingen bekræfter det, den
        skulle afprøve."""
        candidates = scaffold_candidates(
            indexed_session,
            ["livbåde"],
            modes=["lexical", "semantic", "hybrid"],
            candidates_per_mode=5,
        )

        assert candidates
        found_by = {mode for c in candidates for mode in c.found_by}
        assert "semantic" in found_by

    def test_kandidater_er_unikke_pr_soegning(self, indexed_session):
        candidates = scaffold_candidates(
            indexed_session, ["skib"], modes=["lexical", "hybrid"], candidates_per_mode=5
        )
        ids = [c.source_id for c in candidates]
        assert len(ids) == len(set(ids))

    def test_csv_rundtur(self, indexed_session, tmp_path):
        candidates = scaffold_candidates(
            indexed_session, ["brand passagerskib"], modes=["lexical"], candidates_per_mode=3
        )
        csv_path = write_candidate_csv(candidates, tmp_path / "review.csv")

        # Simulér en fagpersons gennemgang: markér den første som relevant.
        import csv as csv_module

        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv_module.DictReader(handle))
        rows[0]["relevant"] = "ja"
        for row in rows[1:]:
            row["relevant"] = "nej"

        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv_module.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        eval_set = read_reviewed_csv(csv_path, corpus="fixture", synthetic=True)

        assert len(eval_set.queries) == 1
        assert eval_set.queries[0].query == "brand passagerskib"
        assert eval_set.queries[0].relevant == {rows[0]["source_id"]}
        assert eval_set.synthetic is True

    def test_ugennemgaaet_soegning_udelades(self, tmp_path):
        """'Det nåede jeg ikke' og 'her findes intet' er ikke det samme.
        Blandes de sammen, straffes søgemaskinen for menneskets tid."""
        path = tmp_path / "review.csv"
        path.write_text(
            "query,source_id,title,authority,status,found_by,best_rank,relevant,notes\n"
            "gennemgået,DOC-1,Titel,,,lexical,1,ja,\n"
            "ikke gennemgået,DOC-2,Titel,,,lexical,1,,\n",
            encoding="utf-8-sig",
        )
        eval_set = read_reviewed_csv(path, corpus="test")

        assert [q.query for q in eval_set.queries] == ["gennemgået"]

    def test_gennemgaaet_uden_traef_bliver_negativ_kontrol(self, tmp_path):
        path = tmp_path / "review.csv"
        path.write_text(
            "query,source_id,title,authority,status,found_by,best_rank,relevant,notes\n"
            "intet passer,DOC-1,Titel,,,lexical,1,nej,\n",
            encoding="utf-8-sig",
        )
        eval_set = read_reviewed_csv(path, corpus="test")

        assert len(eval_set.negative_controls) == 1
        assert eval_set.negative_controls[0].query == "intet passer"

    def test_ja_accepteres_i_flere_skrivemaader(self, tmp_path):
        """Filen udfyldes i Excel af et menneske, ikke af en parser."""
        path = tmp_path / "review.csv"
        path.write_text(
            "query,source_id,title,authority,status,found_by,best_rank,relevant,notes\n"
            "a,DOC-1,T,,,lexical,1,JA,\n"
            "b,DOC-2,T,,,lexical,1,x,\n"
            "c,DOC-3,T,,,lexical,1,Yes,\n",
            encoding="utf-8-sig",
        )
        eval_set = read_reviewed_csv(path, corpus="test")
        assert all(q.relevant for q in eval_set.queries)

    def test_manglende_kolonner_forklares(self, tmp_path):
        path = tmp_path / "daarlig.csv"
        path.write_text("noget,andet\n1,2\n", encoding="utf-8-sig")
        with pytest.raises(ValueError, match="mangler kolonnerne"):
            read_reviewed_csv(path, corpus="test")

    def test_helt_umarkeret_fil_afvises(self, tmp_path):
        path = tmp_path / "review.csv"
        path.write_text(
            "query,source_id,title,authority,status,found_by,best_rank,relevant,notes\n"
            "a,DOC-1,T,,,lexical,1,,\n",
            encoding="utf-8-sig",
        )
        with pytest.raises(ValueError, match="ingen søgninger var markeret"):
            read_reviewed_csv(path, corpus="test")

    def test_soegninger_hentes_fra_soegeloggen(self, indexed_session, embedding_provider):
        """De søgninger brugerne faktisk stiller er bedre end dem en
        udvikler finder på."""
        from app.services.evaluation import queries_from_search_log
        from app.services.search import QueryLogService

        log = QueryLogService(indexed_session, embedding_provider)
        log.record("brand passagerskib", result_count=3, mode="hybrid")
        log.record("brand passagerskib", result_count=3, mode="hybrid")
        log.record("kvantemekanisk vandpolo", result_count=0, mode="hybrid")

        queries = queries_from_search_log(indexed_session, limit=10, include_empty=True)

        assert "brand passagerskib" in queries
        assert "kvantemekanisk vandpolo" in queries

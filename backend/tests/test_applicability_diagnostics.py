"""Diagnostik af korpusdækning.

Rapporten skal kunne bære en beslutning. To egenskaber er derfor vigtigere end
resten: den må ikke anbefale et bredere søgevindue på grundlag af en
skønsbestemmelse langt nede i teksten, og den skal skelne mellem en ny vending,
der giver en brugbar regel, og en, der blot giver endnu et tomt udkast.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.applicability.diagnostics import (
    analyze_corpus,
    analyze_drafts,
    render_corpus_report,
    render_draft_report,
)

MED_SKOP = """Bekendtgørelse om sikkerhed på passagerskibe

Kapitel 1
Anvendelsesområde

§ 1. Bekendtgørelsen finder anvendelse på passagerskibe med en bruttotonnage på 500 eller derover.
"""

UDEN_VENDING_MEN_MED_TAL = """Bekendtgørelse om radioudstyr i skibe

§ 1. Bekendtgørelsen fastsætter krav til radioudstyr i lastskibe med en bruttotonnage på 300 eller derover.
"""

UDEN_VENDING_UDEN_TAL = """Bekendtgørelse om uddannelse af søfarende

§ 1. Bekendtgørelsen fastsætter krav til uddannelse og kvalifikationer for søfarende.
"""

KUN_SKOEN_LANGT_NEDE = """Bekendtgørelse om tilsyn

§ 1. Reglerne i denne bekendtgørelse administreres af Søfartsstyrelsen.

§ 2. Rederiet skal føre journal.

§ 3. Journalen opbevares i fem år.

§ 4. Skibsføreren underskriver journalen.

§ 5. Journalen fremvises på forlangende.

§ 6. Journalen kan føres elektronisk.

§ 7. Søfartsstyrelsen kan ved havnestatskontrol forlange en øvelse gennemført.
"""

TOM_REGEL = """Bekendtgørelse om forholdene om bord

Kapitel 1
Anvendelsesområde

§ 1. Bekendtgørelsen finder anvendelse på forholdene om bord i skibe.
"""

UDEN_PARAGRAFFER = """Vejledning om god skik

Denne vejledning beskriver praksis. Den indeholder ingen bestemmelser.
"""


def store(session, title: str, content: str, *, is_maritime: bool = True):
    from app.models import Document, DocumentVersion

    document = Document(
        source="test",
        source_id=f"DIAG-{title[:24]}",
        title=title,
        display_title=title,
        authority="Søfartsstyrelsen",
        document_type="Bekendtgørelse",
        status="Gældende",
        published_date=date(2020, 1, 1),
        is_maritime=is_maritime,
        maritime_score=90 if is_maritime else 5,
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        content=content,
        content_hash=f"{document.id:064d}",
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(version)
    session.flush()
    document.current_version_id = version.id
    session.flush()
    return document


@pytest.fixture()
def korpus(session):
    store(session, "Bekendtgørelse om sikkerhed på passagerskibe", MED_SKOP)
    store(session, "Bekendtgørelse om radioudstyr i skibe", UDEN_VENDING_MEN_MED_TAL)
    store(session, "Bekendtgørelse om uddannelse af søfarende", UDEN_VENDING_UDEN_TAL)
    store(session, "Bekendtgørelse om tilsyn", KUN_SKOEN_LANGT_NEDE)
    # Et skop uden en eneste målbar betingelse — kilden til de tomme udkast.
    store(session, "Bekendtgørelse om forholdene om bord", TOM_REGEL)
    store(session, "Vejledning om god skik", UDEN_PARAGRAFFER)
    session.commit()
    return session


class TestKorpusrapport:
    def test_taeller_dokumenter_med_og_uden_udkast(self, korpus):
        report = analyze_corpus(korpus)
        assert report.documents_total == 6
        assert report.with_drafts == 2
        assert report.without_drafts == 4

    def test_tekst_uden_paragraffer_faar_sin_egen_aarsag(self, korpus):
        report = analyze_corpus(korpus)
        assert report.reasons["ingen_paragraffer"] == 1

    def test_skoen_langt_nede_udloeser_ikke_anbefaling_om_bredere_vindue(self, korpus):
        """En skønsbestemmelse i § 7 er ikke et anvendelsesområde.

        Talte den med, ville rapporten anbefale et bredere søgevindue, som kun
        ville trække tilsyns- og dispensationsbestemmelser ind — altså gøre
        køen værre, ikke bedre.
        """
        report = analyze_corpus(korpus)
        assert report.recoverable_by_window == 0
        assert report.reasons["kun_definitioner"] == 1

    def test_skelner_mellem_brugbar_og_tom_ny_vending(self, korpus):
        """Det tal, der afgør om en ny vending er værd at tilføje."""
        report = analyze_corpus(korpus)
        # Radioudstyr har en BT-grænse; uddannelse har ingen målbar betingelse.
        assert report.marker_would_yield_conditions == 1
        assert report.marker_would_yield_empty >= 1

    def test_miner_de_vendinger_teksten_faktisk_bruger(self, korpus):
        report = analyze_corpus(korpus)
        phrases = " ".join(phrase for phrase, _ in report.missed_markers.most_common())
        assert "fastsaetter krav til" in phrases

    def test_stikproever_peger_paa_konkrete_dokumenter(self, korpus):
        report = analyze_corpus(korpus, samples_per_reason=3)
        samples = report.samples["ingen_skopmarkoer"]
        assert samples and samples[0].document_id
        assert samples[0].sample

    def test_rapporten_kan_gengives_som_tekst(self, korpus):
        text = render_corpus_report(analyze_corpus(korpus))
        assert "KORPUSDÆKNING" in text
        assert "KANDIDATVENDINGER" in text

    def test_rapporten_skriver_ikke_i_databasen(self, korpus):
        from app.models import ApplicabilityRule

        before = korpus.query(ApplicabilityRule).count()
        analyze_corpus(korpus)
        assert korpus.query(ApplicabilityRule).count() == before


class TestUdkastrapport:
    def test_goer_status_over_hvad_der_staar_i_reglerne(self, korpus):
        from app.services.applicability import ApplicabilityService

        ApplicabilityService(korpus).run_draft_generation(scope="maritime")
        report = analyze_drafts(korpus)

        assert report.rules_total >= 1
        assert report.field_frequency["vessel.all_types"] >= 1
        assert report.by_review_status["draft"] == report.rules_total
        assert "UDKAST" in render_draft_report(report)

    def test_tom_base_giver_en_laesbar_rapport(self, session):
        report = analyze_drafts(session)
        assert report.rules_total == 0
        assert "Ingen regler" in render_draft_report(report)


class TestTriage:
    def _drafts(self, session):
        from app.services.applicability import ApplicabilityService

        ApplicabilityService(session).run_draft_generation(scope="maritime")

    def test_toerkoersel_aendrer_intet(self, korpus, capsys):
        from app.cli import cmd_applicability_triage
        import argparse

        self._drafts(korpus)
        from app.models import ApplicabilityRule

        before = {r.id: r.review_status for r in korpus.query(ApplicabilityRule).all()}
        cmd_applicability_triage(argparse.Namespace(actor="jacob", limit=None, yes=False))
        korpus.expire_all()
        after = {r.id: r.review_status for r in korpus.query(ApplicabilityRule).all()}
        assert before == after
        output = capsys.readouterr().out
        assert "Intet er ændret" in output
        assert "--yes" in output

    def test_udkast_uden_betingelser_flyttes_og_kan_findes_igen(self, korpus):
        import argparse

        from app.cli import cmd_applicability_triage
        from app.models import ApplicabilityCondition, ApplicabilityRule

        self._drafts(korpus)
        korpus.commit()

        empty_ids = [
            rule.id
            for rule in korpus.query(ApplicabilityRule).all()
            if not [
                c
                for c in korpus.query(ApplicabilityCondition)
                .filter(
                    ApplicabilityCondition.rule_id == rule.id,
                    ApplicabilityCondition.node_type == "atom",
                    ApplicabilityCondition.clause_kind == "inclusion",
                )
                .all()
            ]
        ]

        assert empty_ids, "fixturen skal indeholde mindst ét udkast uden betingelser"

        cmd_applicability_triage(argparse.Namespace(actor="jacob", limit=None, yes=True))
        korpus.expire_all()

        for rule_id in empty_ids:
            rule = korpus.get(ApplicabilityRule, rule_id)
            assert rule.review_status == "needs_changes"
            # Ikke slettet, og flytningen står i sporet.
            events = [e.event_type for e in rule.review_events]
            assert "REOPENED" in events

        for rule in korpus.query(ApplicabilityRule).all():
            if rule.id not in empty_ids:
                assert rule.review_status == "draft"

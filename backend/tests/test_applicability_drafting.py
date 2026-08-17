"""Udkastgeneratoren: fra rigtig lovtekst til forslag til regler.

Den vigtigste påstand i filen er den negative: et maskinelt udtræk kan ikke
sætte dækningsgraden til ``complete`` og dermed ikke selv gøre et regex-match
til en juridisk konklusion.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.applicability import (
    Comparator,
    CoverageLevel,
    ReviewStatus,
    build_rule_drafts,
)
from app.services.applicability.drafting import classify_unit, extract_conditions
from app.services.applicability.rules import CitationKind

BEKENDTGOERELSE = """Bekendtgørelse om sikkerhed på passagerskibe

I medfør af § 1 i lov om sikkerhed til søs fastsættes:

Kapitel 1
Anvendelsesområde

§ 1. Bekendtgørelsen finder anvendelse på passagerskibe med en bruttotonnage på 500 eller derover i international fart.
Stk. 2. Bekendtgørelsen finder ikke anvendelse på fiskeskibe.
Stk. 3. Søfartsstyrelsen kan efter ansøgning fritage skibe, der udelukkende anvendes i havnefart.

§ 2. I denne bekendtgørelse forstås ved passagerskib et skib, der medfører flere end 12 passagerer.

Kapitel 2
Krav til redningsmidler

§ 3. Rederiet skal sikre, at kravene i § 1 er opfyldt, jf. § 2. Redningsmidler skal synes årligt.
"""

FISKESKIBE = """Bekendtgørelse om fiskeskibes bygning og udstyr

Kapitel 1
Anvendelsesområde

§ 1. Bekendtgørelsen finder anvendelse på fiskeskibe med et dimensionstal under 100.
Stk. 2. For fiskeskibe med en længde på 15 meter og derover gælder tillige kapitel 4.
"""

UDEN_SKOP = """Vejledning om god skik

§ 1. Denne vejledning beskriver Søfartsstyrelsens praksis.
"""


def drafts_for(text: str, **kwargs):
    return build_rule_drafts(
        document_id=1,
        document_version_id=7,
        content=text,
        title=kwargs.pop("title", "Bekendtgørelse om sikkerhed på passagerskibe"),
        authority="Søfartsstyrelsen",
        document_type="Bekendtgørelse",
        in_force_from=date(2019, 7, 1),
        status_state="in_force",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Klassifikation
# ---------------------------------------------------------------------------


class TestKlassifikation:
    def test_negationen_laeses_foer_det_positive_udtryk(self):
        """"finder ikke anvendelse" indeholder "finder anvendelse"."""
        assert classify_unit("Bekendtgørelsen finder ikke anvendelse på fiskeskibe.") is (
            CitationKind.EXCLUSION
        )
        assert classify_unit("Bekendtgørelsen finder anvendelse på fiskeskibe.") is (
            CitationKind.INCLUSION
        )

    def test_skoen_og_definition_genkendes(self):
        assert classify_unit("Søfartsstyrelsen kan fritage skibe under 20 BT.") is (
            CitationKind.DISCRETION
        )
        assert classify_unit("I denne bekendtgørelse forstås ved skib et fartøj.") is (
            CitationKind.DEFINITION
        )

    def test_en_bestemmelse_uden_skopmarkoer_er_ikke_et_anvendelsesomraade(self):
        assert classify_unit("Rederiet skal sikre, at kravene er opfyldt.") is None


# ---------------------------------------------------------------------------
# Udtræk
# ---------------------------------------------------------------------------


class TestUdtraek:
    def test_bruttotonnage_med_eksplicit_komparator(self):
        atoms, _ = extract_conditions(
            "Bekendtgørelsen finder anvendelse på lastskibe med en bruttotonnage "
            "på 500 eller derover.",
            "c1",
        )
        gt = next(a for a in atoms if a.field_name == "dim.gross_tonnage")
        assert gt.op is Comparator.GTE
        assert gt.value == 500
        assert gt.confidence == "high"

    def test_dimensionstal_med_omvendt_ordstilling(self):
        atoms, _ = extract_conditions("fiskeskibe med et dimensionstal under 100", "c1")
        dim = next(a for a in atoms if a.field_name == "dim.dimensionstal")
        assert dim.op is Comparator.LT
        assert dim.value == 100

    def test_laengde_i_meter(self):
        atoms, _ = extract_conditions("fiskeskibe med en længde på 15 meter og derover", "c1")
        length = next(a for a in atoms if a.field_name.startswith("dim.length"))
        assert length.op is Comparator.GTE
        assert length.value == 15

    def test_passagerantal(self):
        atoms, _ = extract_conditions("skibe der medfører flere end 12 passagerer", "c1")
        assert any(a.field_name == "persons.passenger_count" for a in atoms)

    def test_skibstype_bliver_til_en_maengde_af_typer(self):
        atoms, _ = extract_conditions("finder anvendelse på passagerskibe", "c1")
        types = next(a for a in atoms if a.field_name == "vessel.all_types")
        assert types.op is Comparator.INTERSECTS
        assert "passenger_ship" in types.value

    def test_alle_naevnte_skibstyper_kommer_med(self):
        """"passagerskibe og lastskibe" nævner to typer — begge skal med.

        Et udtræk, der kun så den første, ville lade et lastskib slippe ud af
        en bestemmelse, det klart er omfattet af. Det er farligere end at
        udtrække for lidt, fordi udkastet så ser rigtigt ud.
        """
        atoms, _ = extract_conditions(
            "Bekendtgørelsen finder anvendelse på passagerskibe og lastskibe med en "
            "bruttotonnage på 500 og derover i international fart.",
            "c1",
        )
        types = next(a for a in atoms if a.field_name == "vessel.all_types")
        assert "passenger_ship" in types.value
        assert "general_cargo_ship" in types.value
        assert types.confidence == "low", "flere typer i samme bestemmelse skal efterprøves"
        assert "kontrollér" in types.note.lower()

    def test_specifik_type_taelles_ikke_ogsaa_som_den_generelle(self):
        atoms, _ = extract_conditions("finder anvendelse på ro-ro-passagerskibe", "c1")
        types = next(a for a in atoms if a.field_name == "vessel.all_types")
        assert types.value == ["ro_ro_passenger_ship"]

    def test_fartsomraade_udtraekkes(self):
        atoms, _ = extract_conditions("på lastskibe i international fart", "c1")
        operation = next(a for a in atoms if a.field_name == "operation.types")
        assert operation.value == ["international_voyage"]

    def test_dansk_talskrivning(self):
        atoms, _ = extract_conditions("en bruttotonnage på 1.500 eller derover", "c1")
        gt = next(a for a in atoms if a.field_name == "dim.gross_tonnage")
        assert gt.value == 1500


# ---------------------------------------------------------------------------
# Hele udkastet
# ---------------------------------------------------------------------------


class TestUdkast:
    @pytest.fixture()
    def draft(self):
        drafts = drafts_for(BEKENDTGOERELSE)
        assert len(drafts) == 1, "kun § 1 bærer anvendelsesområdet"
        return drafts[0]

    def test_udkastet_hoerer_til_paragraf_1(self, draft):
        assert draft.rule_ref == "§ 1"
        assert draft.document_version_id == 7

    def test_inklusionsbetingelser_udtraekkes(self, draft):
        fields = {atom.field_name for atom in draft.inclusion_atoms}
        assert "vessel.all_types" in fields
        assert "dim.gross_tonnage" in fields
        assert "operation.types" in fields

    def test_undtagelsen_bliver_sin_egen_bestemmelse(self, draft):
        assert len(draft.exclusions) == 1
        exclusion = draft.exclusions[0]
        assert exclusion.atoms[0].value == ["fishing_vessel"]

    def test_flere_skibstyper_bliver_en_mangel_anmelderen_kan_handle_paa(self):
        drafts = drafts_for(
            "Kapitel 1\nAnvendelsesområde\n\n"
            "§ 1. Bekendtgørelsen finder anvendelse på passagerskibe og lastskibe "
            "med en bruttotonnage på 500 og derover.\n"
        )
        reasons = " ".join(reason for _, reason in drafts[0].coverage_gaps)
        assert "skibstyper" in reasons.lower()

    def test_skoensbestemmelsen_registreres_som_mangel(self, draft):
        assert len(draft.discretion) == 1
        reasons = " ".join(reason for _, reason in draft.coverage_gaps)
        assert "Skønsbestemmelse" in reasons

    def test_daekningsgraden_bliver_aldrig_complete(self, draft):
        """Kernen. Et regex-match må ikke kunne blive til en juridisk konklusion."""
        assert draft.coverage_level is CoverageLevel.PARTIAL
        assert draft.coverage_level is not CoverageLevel.COMPLETE

    def test_citaterne_er_ordrette_og_kan_slaas_op(self, draft):
        from app.services.legal.structure import normalize_legal_text

        text = normalize_legal_text(BEKENDTGOERELSE)
        for citation in draft.citations:
            assert text[citation.char_start : citation.char_end].strip() == citation.text
            assert len(citation.text_hash) == 64

    def test_definitionsparagraffen_bliver_ikke_en_tom_regel(self):
        drafts = drafts_for(BEKENDTGOERELSE)
        assert all(d.rule_ref != "§ 2" for d in drafts)

    def test_krydshenvisning_bliver_ikke_et_anvendelsesomraade(self):
        drafts = drafts_for(BEKENDTGOERELSE)
        assert all(d.rule_ref != "§ 3" for d in drafts)

    def test_flere_stykker_med_hver_sin_graense(self):
        draft = drafts_for(FISKESKIBE, title="Bekendtgørelse om fiskeskibe")[0]
        fields = {atom.field_name for atom in draft.inclusion_atoms}
        assert "dim.dimensionstal" in fields

    def test_tekst_uden_skop_giver_intet_udkast(self):
        assert drafts_for(UDEN_SKOP, title="Vejledning") == []

    def test_tom_tekst_kaster_ikke(self):
        assert drafts_for("", title="Tom") == []


# ---------------------------------------------------------------------------
# Persistering og gennemgang
# ---------------------------------------------------------------------------


def make_stored_document(session, *, title: str, content: str, is_maritime: bool = True):
    """Opretter et dokument med én version, som en import ville have gjort."""
    from app.models import Document, DocumentVersion

    document = Document(
        source="test",
        source_id=f"TEST-{title[:20]}",
        title=title,
        display_title=title,
        authority="Søfartsstyrelsen",
        document_type="Bekendtgørelse",
        status="Gældende",
        published_date=date(2019, 6, 12),
        effective_date=date(2019, 7, 1),
        is_maritime=is_maritime,
        maritime_score=90 if is_maritime else 5,
    )
    session.add(document)
    session.flush()

    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        content=content,
        content_hash="0" * 64,
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(version)
    session.flush()
    document.current_version_id = version.id
    session.flush()
    return document, version


class TestPersistering:
    def test_kaersel_gemmer_udkast_med_status_draft(self, session):
        from app.models import ApplicabilityRule
        from app.services.applicability import ApplicabilityService

        make_stored_document(
            session, title="Bekendtgørelse om sikkerhed på passagerskibe", content=BEKENDTGOERELSE
        )
        summary = ApplicabilityService(session).run_draft_generation(scope="maritime")

        assert summary.rules_created == 1
        rule = session.query(ApplicabilityRule).one()
        assert rule.review_status == ReviewStatus.DRAFT.value
        assert rule.coverage_level == CoverageLevel.PARTIAL.value
        assert rule.origin == "parser"
        assert rule.document_version_id is not None

    def test_kun_maritime_dokumenter_med_mindre_andet_vaelges(self, session):
        from app.services.applicability import ApplicabilityService

        make_stored_document(
            session,
            title="Bekendtgørelse om folkeskolens undervisning",
            content=BEKENDTGOERELSE,
            is_maritime=False,
        )
        summary = ApplicabilityService(session).run_draft_generation(scope="maritime")
        assert summary.documents_scanned == 0
        assert summary.rules_created == 0

    def test_samme_kaersel_to_gange_giver_ikke_dubletter(self, session):
        from app.services.applicability import ApplicabilityService

        make_stored_document(
            session, title="Bekendtgørelse om sikkerhed på passagerskibe", content=BEKENDTGOERELSE
        )
        service = ApplicabilityService(session)
        service.run_draft_generation(scope="maritime")
        second = service.run_draft_generation(scope="maritime")
        assert second.rules_created == 0
        assert second.rules_unchanged == 1

    def test_udkast_bruges_ikke_af_vurderingen_foer_det_er_godkendt(self, session):
        from app.services.applicability import ApplicabilityService, load_rule_specs

        make_stored_document(
            session, title="Bekendtgørelse om sikkerhed på passagerskibe", content=BEKENDTGOERELSE
        )
        ApplicabilityService(session).run_draft_generation(scope="maritime")

        assert load_rule_specs(session) == []
        assert len(load_rule_specs(session, review_status=None)) == 1

    def test_godkendelse_skrives_i_revisionssporet(self, session):
        from app.models import ApplicabilityRule, RuleReviewStatus
        from app.services.applicability import ApplicabilityService, load_rule_specs
        from app.services.applicability.repository import set_review_status

        make_stored_document(
            session, title="Bekendtgørelse om sikkerhed på passagerskibe", content=BEKENDTGOERELSE
        )
        ApplicabilityService(session).run_draft_generation(scope="maritime")
        rule = session.query(ApplicabilityRule).one()

        set_review_status(
            session,
            rule,
            RuleReviewStatus.APPROVED,
            actor="jacob",
            note="Læst igennem mod kilden.",
            coverage_level=CoverageLevel.COMPLETE,
        )

        assert rule.review_status == "approved"
        assert rule.coverage_level == "complete"
        events = [event.event_type for event in rule.review_events]
        assert "APPROVED" in events and "DRAFTED" in events
        approved = next(e for e in rule.review_events if e.event_type == "APPROVED")
        assert approved.actor == "jacob"
        assert approved.previous_status == "draft"

        specs = load_rule_specs(session)
        assert len(specs) == 1
        assert specs[0].inclusion is not None

    def test_betingelsestraeet_kan_laeses_tilbage_som_domaenegenstande(self, session):
        from app.models import ApplicabilityRule, RuleReviewStatus
        from app.services.applicability import ApplicabilityService, load_rule_specs
        from app.services.applicability.logic import AllOf, Atom
        from app.services.applicability.repository import set_review_status

        make_stored_document(
            session, title="Bekendtgørelse om sikkerhed på passagerskibe", content=BEKENDTGOERELSE
        )
        ApplicabilityService(session).run_draft_generation(scope="maritime")
        rule = session.query(ApplicabilityRule).one()
        set_review_status(session, rule, RuleReviewStatus.APPROVED, actor="jacob")

        spec = load_rule_specs(session)[0]
        assert isinstance(spec.inclusion, AllOf)
        assert all(isinstance(child, Atom) for child in spec.inclusion.of)
        assert len(spec.exclusions) == 1
        assert len(spec.discretion) == 1
        assert spec.citations

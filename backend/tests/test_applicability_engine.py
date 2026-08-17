"""Den deterministiske anvendelighedsmotor.

Rene domænetests — ingen database. Motoren er en funktion af (profil, regel,
indstillinger), og det er præcis den egenskab, der gør en afgørelse
efterprøvelig.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.applicability import (
    AllOf,
    Always,
    AnyOf,
    ApplicabilityRuleSpec,
    Atom,
    Comparator,
    CoverageLevel,
    Dimensions,
    DiscretionClause,
    DiscretionEffect,
    EvalOptions,
    ExclusionClause,
    Jurisdiction,
    Measured,
    OperationType,
    Persons,
    ReviewStatus,
    RuleJurisdiction,
    RuleState,
    RuleStatus,
    ScopeCitation,
    ScopeCoverage,
    Tri,
    UnknownPolicy,
    ValueSource,
    Verdict,
    VesselProfile,
    VesselType,
    derive_facts,
    evaluate_applicability,
    explain_applicability,
    rank_results,
)
from app.services.applicability.logic import kleene_and, kleene_not, kleene_or
from app.services.applicability.rules import CoverageGap

TODAY = date(2026, 8, 17)
OPTIONS = EvalOptions(assessment_date=TODAY)


# ---------------------------------------------------------------------------
# Hjælpere
# ---------------------------------------------------------------------------


def ferry(**overrides) -> VesselProfile:
    base = dict(
        profile_id="prof-ferry",
        vessel_type=VesselType.RO_RO_PASSENGER_SHIP,
        operation_types=[OperationType.DOMESTIC_VOYAGE, OperationType.NEAR_COASTAL],
        dimensions=Dimensions(
            length_overall_m=Measured(87.5, ValueSource.CERTIFICATE),
            breadth_m=Measured(16.2, ValueSource.CERTIFICATE),
            depth_m=Measured(5.4, ValueSource.CERTIFICATE),
            gross_tonnage=Measured(3200, ValueSource.CERTIFICATE),
        ),
        persons=Persons(passenger_count=Measured(420, ValueSource.CERTIFICATE)),
        jurisdiction=Jurisdiction(flag_state="DK", operating_areas=["DK_TERRITORIAL"]),
    )
    base.update(overrides)
    return VesselProfile(**base)


def cargo(**overrides) -> VesselProfile:
    base = dict(
        profile_id="prof-cargo-499",
        vessel_type=VesselType.GENERAL_CARGO_SHIP,
        operation_types=[OperationType.INTERNATIONAL_VOYAGE],
        dimensions=Dimensions(gross_tonnage=Measured(499, ValueSource.CERTIFICATE)),
        jurisdiction=Jurisdiction(flag_state="DK", operating_areas=["EU"]),
    )
    base.update(overrides)
    return VesselProfile(**base)


def rule(
    inclusion,
    *,
    exclusions=None,
    discretion=None,
    coverage=CoverageLevel.COMPLETE,
    gaps=(),
    status=None,
    jurisdiction=None,
    citations=None,
) -> ApplicabilityRuleSpec:
    return ApplicabilityRuleSpec(
        rule_id=1,
        document_id=1,
        rule_ref="§ 1",
        title="Bekendtgørelse om noget maritimt",
        authority="Søfartsstyrelsen",
        document_type="Bekendtgørelse",
        status=status
        or RuleStatus(state=RuleState.IN_FORCE, in_force_from=date(2019, 7, 1)),
        jurisdiction=jurisdiction or RuleJurisdiction(flag_states=("DK",), operating_areas=("*",)),
        inclusion=inclusion,
        exclusions=list(exclusions or []),
        discretion=list(discretion or []),
        citations=citations
        or {
            "c1": ScopeCitation(
                key="c1",
                ref="§ 1, stk. 1",
                text=(
                    "Bekendtgørelsen finder anvendelse på passagerskibe med en "
                    "bruttotonnage på 500 eller derover."
                ),
            ),
            "c2": ScopeCitation(
                key="c2",
                ref="§ 1, stk. 2",
                text="Bekendtgørelsen finder ikke anvendelse på fritidsfartøjer.",
                kind=__import__(
                    "app.services.applicability.rules", fromlist=["CitationKind"]
                ).CitationKind.EXCLUSION,
            ),
        },
        coverage=ScopeCoverage(level=coverage, gaps=[CoverageGap(*g) for g in gaps]),
        review_status=ReviewStatus.APPROVED,
    )


def atom(field_name, op, value, **kwargs) -> Atom:
    kwargs.setdefault("citation_key", "c1")
    return Atom(id=f"a-{field_name}-{op.value}", field_name=field_name, op=op, value=value, **kwargs)


PAX_SHIP = atom("derived.is_passenger_ship", Comparator.EQ, True)
GT_500 = atom("dim.gross_tonnage", Comparator.GTE, 500, tolerance=10)


# ---------------------------------------------------------------------------
# Tre-værdi-logik
# ---------------------------------------------------------------------------


class TestKleene:
    def test_and_er_falsk_saa_snart_et_led_er_falsk(self):
        assert kleene_and([Tri.TRUE, Tri.UNKNOWN, Tri.FALSE]) is Tri.FALSE
        assert kleene_and([Tri.TRUE, Tri.UNKNOWN]) is Tri.UNKNOWN
        assert kleene_and([Tri.TRUE, Tri.TRUE]) is Tri.TRUE

    def test_or_er_sand_saa_snart_et_led_er_sandt(self):
        assert kleene_or([Tri.FALSE, Tri.UNKNOWN, Tri.TRUE]) is Tri.TRUE
        assert kleene_or([Tri.FALSE, Tri.UNKNOWN]) is Tri.UNKNOWN
        assert kleene_or([Tri.FALSE, Tri.FALSE]) is Tri.FALSE

    def test_not_bevarer_ukendt(self):
        assert kleene_not(Tri.UNKNOWN) is Tri.UNKNOWN

    def test_tri_kan_ikke_bruges_som_sandhedsvaerdi(self):
        """`if tri:` ville skjule UNKNOWN. Det skal koste en fejl, ikke en bug."""
        with pytest.raises(TypeError):
            bool(Tri.UNKNOWN)


# ---------------------------------------------------------------------------
# Streng udledning — kernen i denne runde
# ---------------------------------------------------------------------------


class TestStrengUdledning:
    def test_manglende_passagerantal_giver_ukendt_ikke_falsk(self):
        """Skibstypen alene er ikke dokumentation for "ikke et passagerskib".

        Ellers kan én fejlklassificeret fartøjstype give et skråsikkert
        "gælder ikke" på en sikkerhedsbestemmelse.
        """
        facts = derive_facts(cargo())
        assert facts.is_passenger_ship is Tri.UNKNOWN

    def test_oplyst_passagerantal_under_graensen_giver_falsk(self):
        facts = derive_facts(
            cargo(persons=Persons(passenger_count=Measured(4, ValueSource.CERTIFICATE)))
        )
        assert facts.is_passenger_ship is Tri.FALSE

    def test_udtrykkelig_oplysning_om_ingen_passagerer_giver_falsk(self):
        facts = derive_facts(cargo(attributes={"carries_passengers": False}))
        assert facts.is_passenger_ship is Tri.FALSE

    def test_passagerskibstype_giver_sandt_uden_antal(self):
        facts = derive_facts(ferry(persons=Persons()))
        assert facts.is_passenger_ship is Tri.TRUE

    def test_fakta_defineret_ved_skibstypen_maa_gerne_blive_falsk(self):
        """Skibstypen er altid oplyst, så der er positiv dokumentation begge veje."""
        facts = derive_facts(cargo())
        assert facts.is_tanker is Tri.FALSE
        assert facts.is_fishing_vessel is Tri.FALSE
        assert facts.is_cargo_ship is Tri.TRUE

    def test_offshore_uden_oplyst_operationstype_er_ukendt(self):
        facts = derive_facts(cargo(operation_types=[]))
        assert facts.is_offshore_unit is Tri.UNKNOWN

    def test_antagelsen_skrives_saa_den_kan_anfaegtes(self):
        facts = derive_facts(cargo())
        note = next(n for n in facts.derivations if n.key == "is_passenger_ship")
        assert note.basis == "ENGINE_DERIVATION_STRICT"
        assert "passagerantal" in note.text_da.lower()

    def test_dimensionstal_udledes_af_maalene(self):
        profile = ferry()
        facts = derive_facts(profile)
        assert facts.dimensionstal is not None
        assert facts.dimensionstal.value == pytest.approx(87.5 * 16.2 * 5.4, rel=1e-6)
        assert facts.dimensionstal.source is ValueSource.DERIVED

    def test_modstridende_dimensionstal_markeres(self):
        profile = ferry()
        profile.dimensions.dimensionstal = Measured(50, ValueSource.DECLARED)
        assert any(c.code == "dimensionstal_mismatch" for c in derive_facts(profile).conflicts)


# ---------------------------------------------------------------------------
# De fire afgørelser
# ---------------------------------------------------------------------------


class TestAfgoerelser:
    def test_applies(self):
        result = evaluate_applicability(ferry(), rule(AllOf([PAX_SHIP, GT_500])), OPTIONS)
        assert result.verdict is Verdict.APPLIES
        assert result.confidence == 100

    def test_does_not_apply(self):
        profile = ferry(
            vessel_type=VesselType.FISHING_VESSEL,
            persons=Persons(passenger_count=Measured(0, ValueSource.CERTIFICATE)),
            operation_types=[OperationType.FISHING_OPERATION],
        )
        result = evaluate_applicability(profile, rule(AllOf([PAX_SHIP, GT_500])), OPTIONS)
        assert result.verdict is Verdict.DOES_NOT_APPLY

    def test_needs_manual_review_naar_profildata_mangler(self):
        result = evaluate_applicability(cargo(), rule(AllOf([PAX_SHIP, GT_500])), OPTIONS)
        assert result.verdict is Verdict.NEEDS_MANUAL_REVIEW
        assert "derived.is_passenger_ship" in result.missing_fields

    def test_needs_manual_review_naar_skoppet_ikke_er_modelleret(self):
        result = evaluate_applicability(
            ferry(),
            rule(Always(True, "c1"), coverage=CoverageLevel.UNPARSED, gaps=[("c1", "afvejning")]),
            OPTIONS,
        )
        assert result.verdict is Verdict.NEEDS_MANUAL_REVIEW

    def test_possibly_applies_ved_delvis_daekning(self):
        result = evaluate_applicability(
            ferry(),
            rule(AllOf([PAX_SHIP, GT_500]), coverage=CoverageLevel.PARTIAL, gaps=[("c2", "led")]),
            OPTIONS,
        )
        assert result.verdict is Verdict.POSSIBLY_APPLIES
        assert result.confidence < 100

    def test_possibly_applies_naar_undtagelse_ikke_kan_afgoeres(self):
        exclusion = ExclusionClause(
            clause_id="x1",
            condition=atom("attr.hull_material", Comparator.EQ, "wood", citation_key="c2"),
            citation_key="c2",
        )
        result = evaluate_applicability(
            ferry(), rule(AllOf([PAX_SHIP, GT_500]), exclusions=[exclusion]), OPTIONS
        )
        assert result.verdict is Verdict.POSSIBLY_APPLIES
        assert any(o is Tri.UNKNOWN for _, _, o in result.triggered_exclusions)

    def test_udloest_undtagelse_giver_does_not_apply(self):
        exclusion = ExclusionClause(
            clause_id="x1",
            condition=atom("attr.hull_material", Comparator.EQ, "wood", citation_key="c2"),
            citation_key="c2",
        )
        profile = ferry(attributes={"hull_material": "wood"})
        result = evaluate_applicability(
            profile, rule(AllOf([PAX_SHIP, GT_500]), exclusions=[exclusion]), OPTIONS
        )
        assert result.verdict is Verdict.DOES_NOT_APPLY

    def test_499_bt_mod_500_afvises_ikke_lydloest(self):
        """Et nej, der hviler alene på måleusikkerhed, er ikke et rent nej."""
        profile = cargo(persons=Persons(passenger_count=Measured(0, ValueSource.CERTIFICATE)))
        result = evaluate_applicability(profile, rule(AllOf([GT_500])), OPTIONS)
        assert result.verdict is Verdict.POSSIBLY_APPLIES
        near = next(c for c in result.failed if c.field_name == "dim.gross_tonnage")
        assert near.near_threshold is True
        assert near.margin_to_threshold == 1

    def test_skoen_om_udvidelse_kan_trække_et_skib_under_graensen_ind(self):
        discretion = DiscretionClause(
            clause_id="d1",
            authority="Søfartsstyrelsen",
            effect=DiscretionEffect.MAY_EXTEND,
            citation_key="c1",
            condition=atom("dim.gross_tonnage", Comparator.BETWEEN, [100, 499]),
        )
        profile = cargo(dimensions=Dimensions(gross_tonnage=Measured(300, ValueSource.CERTIFICATE)))
        result = evaluate_applicability(
            profile, rule(AllOf([GT_500]), discretion=[discretion]), OPTIONS
        )
        assert result.verdict is Verdict.POSSIBLY_APPLIES
        assert result.applicable_discretion[0].effect is DiscretionEffect.MAY_EXTEND

    def test_skoen_om_fritagelse_blokerer_et_rent_ja(self):
        discretion = DiscretionClause(
            clause_id="d1",
            authority="Søfartsstyrelsen",
            effect=DiscretionEffect.MAY_EXEMPT,
            citation_key="c1",
        )
        result = evaluate_applicability(
            ferry(), rule(AllOf([PAX_SHIP, GT_500]), discretion=[discretion]), OPTIONS
        )
        assert result.verdict is Verdict.POSSIBLY_APPLIES

    def test_unknown_policy_kan_lukke_et_ikke_maritimt_felt(self):
        """Et skib har ikke en institutionstype. Det skal give et rent nej."""
        institution = atom(
            "attr.institution_type",
            Comparator.EQ,
            "folkeskole",
            unknown_policy=UnknownPolicy.TREAT_AS_FALSE,
        )
        result = evaluate_applicability(ferry(), rule(institution), OPTIONS)
        assert result.verdict is Verdict.DOES_NOT_APPLY
        assert result.confidence == 100


# ---------------------------------------------------------------------------
# Porte
# ---------------------------------------------------------------------------


class TestPorte:
    def test_beslutningsvejen_koeres_i_fast_raekkefoelge(self):
        result = evaluate_applicability(ferry(), rule(AllOf([PAX_SHIP, GT_500])), OPTIONS)
        assert [step.gate.value for step in result.decision_path] == [
            "temporal_status",
            "jurisdiction",
            "structured_metadata",
            "thresholds",
            "exclusions",
            "coverage",
        ]

    def test_taerskler_springes_over_naar_metadata_allerede_udelukker(self):
        profile = ferry(
            vessel_type=VesselType.FISHING_VESSEL,
            persons=Persons(passenger_count=Measured(0, ValueSource.CERTIFICATE)),
        )
        result = evaluate_applicability(profile, rule(AllOf([PAX_SHIP, GT_500])), OPTIONS)
        thresholds = next(s for s in result.decision_path if s.gate.value == "thresholds")
        assert thresholds.outcome == "skipped"
        assert "metadata" in thresholds.summary_da

    def test_ophaevet_regel_gaelder_ikke_i_dag(self):
        status = RuleStatus(
            state=RuleState.REPEALED,
            in_force_from=date(2005, 1, 1),
            in_force_to=date(2019, 6, 30),
        )
        result = evaluate_applicability(ferry(), rule(AllOf([PAX_SHIP]), status=status), OPTIONS)
        assert result.verdict is Verdict.DOES_NOT_APPLY
        assert result.decision_path[0].outcome == "false"

    def test_historisk_ret_kan_findes_men_kun_som_muligvis(self):
        status = RuleStatus(
            state=RuleState.REPEALED,
            in_force_from=date(2005, 1, 1),
            in_force_to=date(2019, 6, 30),
        )
        options = EvalOptions(assessment_date=date(2015, 6, 1), status_mode="historical")
        result = evaluate_applicability(ferry(), rule(AllOf([PAX_SHIP]), status=status), options)
        assert result.verdict is Verdict.POSSIBLY_APPLIES
        assert result.historical is True

    def test_ukendt_status_sendes_til_manuel_gennemgang(self):
        status = RuleStatus(state=RuleState.UNKNOWN)
        result = evaluate_applicability(ferry(), rule(AllOf([PAX_SHIP]), status=status), OPTIONS)
        assert result.verdict is Verdict.NEEDS_MANUAL_REVIEW

    def test_uden_for_jurisdiktionen_afvises_foer_betingelserne_laeses(self):
        profile = ferry(jurisdiction=Jurisdiction(flag_state="NO", operating_areas=["NO"]))
        result = evaluate_applicability(profile, rule(AllOf([PAX_SHIP, GT_500])), OPTIONS)
        assert result.verdict is Verdict.DOES_NOT_APPLY
        assert result.matched == []

    def test_havnestatskontrol_kan_bringe_et_fremmed_skib_ind(self):
        profile = ferry(
            jurisdiction=Jurisdiction(
                flag_state="PA", operating_areas=["INTERNATIONAL"], port_states=["DK"]
            )
        )
        spec = rule(
            AllOf([PAX_SHIP, GT_500]),
            jurisdiction=RuleJurisdiction(
                flag_states=("DK",), operating_areas=("*",), port_state_applies=True
            ),
        )
        assert evaluate_applicability(profile, spec, OPTIONS).verdict is Verdict.APPLIES

    def test_manglende_flagstat_giver_manuel_gennemgang(self):
        profile = ferry(jurisdiction=Jurisdiction(operating_areas=["DK_TERRITORIAL"]))
        result = evaluate_applicability(profile, rule(AllOf([PAX_SHIP])), OPTIONS)
        assert result.verdict is Verdict.NEEDS_MANUAL_REVIEW


# ---------------------------------------------------------------------------
# Revision
# ---------------------------------------------------------------------------


class TestRevision:
    def test_samme_input_giver_samme_hash(self):
        spec = rule(AllOf([PAX_SHIP, GT_500]))
        first = evaluate_applicability(ferry(), spec, OPTIONS)
        second = evaluate_applicability(ferry(), spec, OPTIONS)
        assert first.inputs_hash == second.inputs_hash
        assert first.verdict is second.verdict

    def test_hashen_aendrer_sig_naar_profilen_aendrer_sig(self):
        spec = rule(AllOf([PAX_SHIP, GT_500]))
        first = evaluate_applicability(ferry(), spec, OPTIONS)
        second = evaluate_applicability(
            ferry(persons=Persons(passenger_count=Measured(8, ValueSource.CERTIFICATE))),
            spec,
            OPTIONS,
        )
        assert first.inputs_hash != second.inputs_hash

    def test_motoren_erklaerer_at_ingen_sprogmodel_indgik(self):
        result = evaluate_applicability(ferry(), rule(AllOf([PAX_SHIP])), OPTIONS)
        assert result.used_language_model is False
        assert result.deterministic is True
        assert result.supporting_fragments == []

    def test_hver_delafgoerelse_baerer_ordret_skoptekst(self):
        result = evaluate_applicability(ferry(), rule(AllOf([PAX_SHIP, GT_500])), OPTIONS)
        keys = {citation.key for citation in result.citations}
        assert "c1" in keys
        for condition in result.matched:
            if condition.citation_key:
                assert condition.citation_key in keys

    def test_skoennede_maalevaerdier_kan_regnes_som_ukendte(self):
        profile = ferry(
            dimensions=Dimensions(gross_tonnage=Measured(3200, ValueSource.ESTIMATED))
        )
        spec = rule(AllOf([PAX_SHIP, GT_500]))
        strict = evaluate_applicability(
            profile,
            spec,
            EvalOptions(assessment_date=TODAY, treat_estimated_as_unknown=True),
        )
        assert strict.verdict is Verdict.NEEDS_MANUAL_REVIEW
        lenient = evaluate_applicability(profile, spec, OPTIONS)
        assert lenient.verdict is Verdict.APPLIES
        assert lenient.confidence < 100


# ---------------------------------------------------------------------------
# Forklaring og rangering
# ---------------------------------------------------------------------------


class TestForklaring:
    def test_forklaringen_gengiver_lovteksten_uaendret(self):
        result = evaluate_applicability(ferry(), rule(AllOf([PAX_SHIP, GT_500])), OPTIONS)
        explanation = explain_applicability(result)
        quoted = next(b.quote for b in explanation.bullets if b.quote)
        assert quoted in {c.text for c in result.citations}
        assert quoted in explanation.plain_text

    def test_en_ikke_udloest_undtagelse_vises_ikke_som_fejlet_betingelse(self):
        exclusion = ExclusionClause(
            clause_id="x1",
            condition=atom("vessel.all_types", Comparator.INTERSECTS, ["pleasure_craft"], citation_key="c2"),
            citation_key="c2",
        )
        result = evaluate_applicability(
            ferry(), rule(AllOf([PAX_SHIP, GT_500]), exclusions=[exclusion]), OPTIONS
        )
        explanation = explain_applicability(result)
        assert result.verdict is Verdict.APPLIES
        assert [b for b in explanation.bullets if b.tone == "mismatch"] == []

    def test_manglende_felt_bliver_til_et_naeste_skridt(self):
        result = evaluate_applicability(cargo(), rule(AllOf([PAX_SHIP])), OPTIONS)
        explanation = explain_applicability(result)
        assert any(step.action == "supply_field" for step in explanation.next_steps)

    def test_revisionssporet_naevner_hash_og_kildeforbehold(self):
        result = evaluate_applicability(ferry(), rule(AllOf([PAX_SHIP])), OPTIONS)
        text = explain_applicability(result).plain_text
        assert "inputhash" in text
        assert "Retsinformation" in text


class TestRangering:
    def _results(self):
        specs = []
        for index, (inclusion, coverage) in enumerate(
            [
                (AllOf([PAX_SHIP, GT_500]), CoverageLevel.COMPLETE),
                (AllOf([PAX_SHIP]), CoverageLevel.PARTIAL),
                (AllOf([atom("derived.is_fishing_vessel", Comparator.EQ, True)]), CoverageLevel.COMPLETE),
                (Always(True, "c1"), CoverageLevel.UNPARSED),
            ],
            start=1,
        ):
            spec = rule(inclusion, coverage=coverage, gaps=[("c1", "led")] if coverage is not CoverageLevel.COMPLETE else [])
            spec.rule_id = index
            specs.append(spec)
        return [evaluate_applicability(ferry(), spec, OPTIONS) for spec in specs]

    def test_sorteres_efter_afgoerelse_foerst(self):
        ranked = rank_results(self._results())
        order = [entry.result.verdict for entry in ranked]
        assert order[0] is Verdict.APPLIES
        assert order[-1] is Verdict.DOES_NOT_APPLY

    def test_manuel_gennemgang_rangeres_over_gaelder_ikke(self):
        ranked = rank_results(self._results())
        verdicts = [entry.result.verdict for entry in ranked]
        assert verdicts.index(Verdict.NEEDS_MANUAL_REVIEW) < verdicts.index(Verdict.DOES_NOT_APPLY)

    def test_rangordenen_er_stabil(self):
        first = [entry.result.rule_id for entry in rank_results(self._results())]
        second = [entry.result.rule_id for entry in rank_results(self._results())]
        assert first == second

    def test_kan_udelade_regler_der_ikke_gaelder(self):
        ranked = rank_results(self._results(), drop_non_applicable=True)
        assert all(entry.result.verdict is not Verdict.DOES_NOT_APPLY for entry in ranked)


# ---------------------------------------------------------------------------
# Sammensatte træer
# ---------------------------------------------------------------------------


def test_any_gren_kan_redde_en_regel():
    inclusion = AnyOf(
        [
            atom("derived.is_fishing_vessel", Comparator.EQ, True),
            AllOf([PAX_SHIP, GT_500]),
        ]
    )
    assert evaluate_applicability(ferry(), rule(inclusion), OPTIONS).verdict is Verdict.APPLIES


def test_ukendt_i_en_gren_forplanter_sig_ikke_naar_en_anden_er_sand():
    inclusion = AnyOf([PAX_SHIP, atom("dim.deadweight_tonnes", Comparator.GTE, 1000)])
    assert evaluate_applicability(ferry(), rule(inclusion), OPTIONS).verdict is Verdict.APPLIES

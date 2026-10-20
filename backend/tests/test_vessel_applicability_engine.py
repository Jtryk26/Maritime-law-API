"""Vessel Applicability 2.0 — den rene motor.

Hver test er en påstand om beslutningsvejen. Ingen database, intet netværk.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.vessel_applicability import (
    ApplicabilityRule,
    ConditionOutcome,
    Verdict,
    VesselProfile,
    evaluate_rule,
    evaluate_rules,
    supplied_fields,
)
from app.services.vessel_applicability.engine import VALID_OPERATORS, validate_condition
from app.services.vessel_applicability.models import (
    PROFILE_FIELD_KINDS,
    EvaluationCondition,
    FieldKind,
    Operator,
)

FERRY = {
    "imo_number": "9123456",
    "vessel_name": "M/F Testfærgen",
    "flag_state": "dk",
    "ship_register": "DIS",
    "primary_type": "passenger",
    "sub_types": ["ro-ro"],
    "gross_tonnage": 3200,
    "length_overall_m": 87.5,
    "max_passengers": 420,
    "operating_area": "international",
    "keel_laid_date": "2010-03-01",
    "assessment_date": "2026-10-20",
    "carries_dangerous_goods": False,
    "special_operations": [],
}


def profile(**overrides) -> VesselProfile:
    data = {**FERRY, **overrides}
    return VesselProfile(**{k: v for k, v in data.items() if v is not ...})


def cond(field: str, op: str, target) -> dict:
    return {"field": field, "operator": op, "target_value": target}


def rule(**overrides) -> ApplicabilityRule:
    data = {
        "rule_id": "BEK-FIRE-4-2",
        "legal_source_id": "BEK-2019-1234",
        "citation": "Bekendtgørelse om brandsikring § 4, stk. 2",
        "topic_category": "fire_protection",
        "status": "APPROVED",
        "source_url": "https://www.retsinformation.dk/eli/lta/2019/1234",
        "inclusion_conditions": [
            cond("primary_type", "eq", "passenger"),
            cond("gross_tonnage", "gte", 500),
        ],
    }
    data.update(overrides)
    return ApplicabilityRule(**data)


# ---------------------------------------------------------------------------
# Den lykkelige vej og sporet
# ---------------------------------------------------------------------------


def test_alle_betingelser_opfyldt_giver_applies_med_fuldt_spor():
    result = evaluate_rule(rule(), profile())

    assert result.verdict is Verdict.APPLIES
    assert result.citation == "Bekendtgørelse om brandsikring § 4, stk. 2"
    assert result.legal_source_id == "BEK-2019-1234"
    assert result.source_url.startswith("https://www.retsinformation.dk")
    assert result.missing_fields == []
    assert result.matched_attributes == {"primary_type": "passenger", "gross_tonnage": 3200.0}
    assert [s.step for s in result.steps] == [
        "temporal_validity", "keel_laid", "exclusions", "missing_data", "inclusions",
    ]
    assert all(c.outcome is ConditionOutcome.MET for c in result.conditions)
    assert any("3200" in reason for reason in result.reasons)


def test_samme_input_giver_samme_svar():
    a = evaluate_rule(rule(), profile())
    b = evaluate_rule(rule(), profile())
    assert a.model_dump() == b.model_dump()


def test_tekst_sammenlignes_uden_hensyn_til_store_bogstaver():
    r = rule(inclusion_conditions=[
        cond("flag_state", "eq", "dk"),
        cond("ship_register", "in", ["dis", "das"]),
    ])
    assert evaluate_rule(r, profile()).verdict is Verdict.APPLIES


# ---------------------------------------------------------------------------
# 1. Gyldighed
# ---------------------------------------------------------------------------


def test_regel_ikke_traadt_i_kraft():
    result = evaluate_rule(rule(valid_from=date(2027, 1, 1)), profile())
    assert result.verdict is Verdict.DOES_NOT_APPLY
    assert result.steps[-1].step == "temporal_validity"
    assert result.conditions == []  # intet blev prøvet


def test_regel_ophaevet():
    result = evaluate_rule(rule(valid_to=date(2020, 12, 31)), profile())
    assert result.verdict is Verdict.DOES_NOT_APPLY
    assert "til og med 2020-12-31" in result.reasons[0]


def test_gyldighedsperiodens_graenser_er_inklusive():
    on = profile(assessment_date="2026-10-20")
    assert evaluate_rule(rule(valid_from=date(2026, 10, 20)), on).verdict is Verdict.APPLIES
    assert evaluate_rule(rule(valid_to=date(2026, 10, 20)), on).verdict is Verdict.APPLIES


# ---------------------------------------------------------------------------
# 2. Kølstrækning
# ---------------------------------------------------------------------------


def test_nyt_skib_regel_rammer_ikke_eksisterende_skib():
    result = evaluate_rule(rule(keel_laid_after=date(2012, 1, 1)), profile())
    assert result.verdict is Verdict.DOES_NOT_APPLY
    assert result.steps[-1].step == "keel_laid"


def test_keel_laid_after_er_inklusiv_og_keel_laid_before_eksklusiv():
    same_day = profile(keel_laid_date="2009-01-01")
    new_ships = rule(keel_laid_after=date(2009, 1, 1))
    old_ships = rule(keel_laid_before=date(2009, 1, 1))
    assert evaluate_rule(new_ships, same_day).verdict is Verdict.APPLIES
    assert evaluate_rule(old_ships, same_day).verdict is Verdict.DOES_NOT_APPLY


def test_vaesentlig_ombygning_i_vinduet_giver_possibly_applies():
    converted = profile(major_conversion_date="2015-06-01")
    result = evaluate_rule(rule(keel_laid_after=date(2012, 1, 1)), converted)
    assert result.verdict is Verdict.POSSIBLY_APPLIES
    assert result.steps[1].outcome == "flagged"
    assert any("ombygning" in reason for reason in result.reasons)


def test_ombygning_efter_skaeringsdatoen_taeller_ikke():
    future = profile(major_conversion_date="2027-06-01")
    result = evaluate_rule(rule(keel_laid_after=date(2012, 1, 1)), future)
    assert result.verdict is Verdict.DOES_NOT_APPLY


def test_ombygning_redder_ikke_en_regel_skibet_ellers_falder_udenfor():
    converted = profile(major_conversion_date="2015-06-01", gross_tonnage=200)
    result = evaluate_rule(rule(keel_laid_after=date(2012, 1, 1)), converted)
    assert result.verdict is Verdict.DOES_NOT_APPLY


# ---------------------------------------------------------------------------
# 3. Undtagelser
# ---------------------------------------------------------------------------


def test_opfyldt_undtagelse_giver_does_not_apply():
    r = rule(exclusion_conditions=[cond("operating_area", "eq", "international")])
    result = evaluate_rule(r, profile())
    assert result.verdict is Verdict.DOES_NOT_APPLY
    assert result.reasons[0].startswith("Undtaget:")


def test_opfyldt_undtagelse_afgoer_selv_med_manglende_data():
    r = rule(
        inclusion_conditions=[cond("crew_capacity", "gte", 10)],
        exclusion_conditions=[cond("primary_type", "eq", "passenger")],
    )
    assert evaluate_rule(r, profile()).verdict is Verdict.DOES_NOT_APPLY


def test_undtagelse_med_ukendt_felt_kan_ikke_afvises():
    r = rule(exclusion_conditions=[cond("voyage_type", "eq", "short_international")])
    result = evaluate_rule(r, profile())
    assert result.verdict is Verdict.NEEDS_MANUAL_REVIEW
    assert result.missing_fields == ["voyage_type"]


# ---------------------------------------------------------------------------
# 4. Manglende data — ingen lukket-verden-slutninger
# ---------------------------------------------------------------------------


def test_manglende_felt_giver_needs_manual_review_og_navngiver_feltet():
    r = rule(inclusion_conditions=[cond("length_rule_m", "gte", 24)])
    result = evaluate_rule(r, profile())
    assert result.verdict is Verdict.NEEDS_MANUAL_REVIEW
    assert result.missing_fields == ["length_rule_m"]
    assert result.conditions[0].outcome is ConditionOutcome.MISSING


def test_standardvaerdi_er_ikke_en_oplysning():
    """``max_passengers = 0`` fra skemaet må ikke blive til "ingen passagerer"."""
    p = profile(max_passengers=...)
    assert "max_passengers" not in supplied_fields(p)
    r = rule(inclusion_conditions=[cond("max_passengers", "gt", 12)])
    result = evaluate_rule(r, p)
    assert result.verdict is Verdict.NEEDS_MANUAL_REVIEW
    assert result.missing_fields == ["max_passengers"]


def test_eksplicit_standardvaerdi_er_en_oplysning():
    p = profile(max_passengers=0)
    assert "max_passengers" in supplied_fields(p)
    r = rule(inclusion_conditions=[cond("max_passengers", "gt", 12)])
    assert evaluate_rule(r, p).verdict is Verdict.DOES_NOT_APPLY


def test_tom_liste_der_ikke_er_oplyst_er_ukendt():
    p = profile(sub_types=...)
    r = rule(inclusion_conditions=[cond("sub_types", "contains", "ro-ro")])
    assert evaluate_rule(r, p).verdict is Verdict.NEEDS_MANUAL_REVIEW


def test_skaeringsdato_regnes_altid_som_oplyst():
    p = profile(assessment_date=...)
    assert "assessment_date" in supplied_fields(p)


def test_beviseligt_ikke_opfyldt_inklusion_goer_manglende_felter_ligegyldige():
    r = rule(inclusion_conditions=[
        cond("primary_type", "eq", "fishing"),
        cond("length_rule_m", "gte", 24),
    ])
    result = evaluate_rule(r, profile())
    assert result.verdict is Verdict.DOES_NOT_APPLY
    assert result.missing_fields == []
    missing_step = next(s for s in result.steps if s.step == "missing_data")
    assert "uden betydning" in missing_step.detail


# ---------------------------------------------------------------------------
# 5. Inklusion
# ---------------------------------------------------------------------------


def test_ikke_opfyldt_taerskel_giver_does_not_apply():
    result = evaluate_rule(rule(), profile(gross_tonnage=499))
    assert result.verdict is Verdict.DOES_NOT_APPLY
    assert result.reasons == [
        "Ikke omfattet: gross_tonnage gte 500: faktisk værdi 499.0 → ikke opfyldt"
    ]
    assert result.matched_attributes == {"primary_type": "passenger"}


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (cond("gross_tonnage", "eq", 3200), True),
        (cond("gross_tonnage", "neq", 3200), False),
        (cond("gross_tonnage", "gt", 3200), False),
        (cond("gross_tonnage", "gte", 3200), True),
        (cond("gross_tonnage", "lt", 3200.5), True),
        (cond("gross_tonnage", "lte", 3199), False),
        (cond("gross_tonnage", "in", [100, 3200]), True),
        (cond("primary_type", "in", ["cargo", "tanker"]), False),
        (cond("sub_types", "contains", "RO-RO"), True),
        (cond("sub_types", "in", ["high-speed-craft", "ro-ro"]), True),
        (cond("special_operations", "contains", "towing"), False),
        (cond("keel_laid_date", "lt", "2011-01-01"), True),
        (cond("keel_laid_date", "gte", "2011-01-01"), False),
        (cond("carries_dangerous_goods", "eq", False), True),
    ],
)
def test_operatorer(condition, expected):
    r = rule(inclusion_conditions=[condition])
    verdict = evaluate_rule(r, profile()).verdict
    assert verdict is (Verdict.APPLIES if expected else Verdict.DOES_NOT_APPLY)


# ---------------------------------------------------------------------------
# Defekte regler: fravær af viden, ikke viden om fravær
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "condition",
    [
        cond("hull_colour", "eq", "red"),            # ukendt felt
        cond("flag_state", "gt", "DK"),              # meningsløs operator
        cond("gross_tonnage", "gte", "500"),         # tal som tekst
        cond("gross_tonnage", "gte", True),          # bool er ikke et tal
        cond("keel_laid_date", "lt", "1. jan 2009"), # ikke en ISO-dato
        cond("primary_type", "in", []),              # tom liste
        cond("carries_dangerous_goods", "eq", "ja"),
    ],
)
def test_ugyldig_betingelse_giver_altid_needs_manual_review(condition):
    # Kombineret med en beviseligt ikke-opfyldt betingelse: selv da må en
    # defekt regel ikke sige "gælder ikke".
    r = rule(inclusion_conditions=[cond("primary_type", "eq", "fishing"), condition])
    result = evaluate_rule(r, profile())
    assert result.verdict is Verdict.NEEDS_MANUAL_REVIEW
    assert any(c.outcome is ConditionOutcome.INVALID for c in result.conditions)
    assert validate_condition(EvaluationCondition(**condition)) is not None


def test_regel_uden_inklusionsbetingelser_afvises():
    with pytest.raises(ValueError):
        rule(inclusion_conditions=[])


def test_omvendte_datoer_afvises():
    with pytest.raises(ValueError):
        rule(valid_from=date(2025, 1, 1), valid_to=date(2024, 1, 1))
    with pytest.raises(ValueError):
        rule(keel_laid_after=date(2025, 1, 1), keel_laid_before=date(2025, 1, 1))


# ---------------------------------------------------------------------------
# Registre
# ---------------------------------------------------------------------------


def test_feltregisteret_daekker_praecis_profilens_felter():
    assert set(PROFILE_FIELD_KINDS) == set(VesselProfile.model_fields)


def test_hver_felttype_har_mindst_en_operator():
    assert set(VALID_OPERATORS) == set(FieldKind)
    used = set().union(*VALID_OPERATORS.values())
    assert used == set(Operator)


def test_evaluate_rules_er_sorteret_stabilt():
    rules = [
        rule(rule_id="B", topic_category="manning"),
        rule(rule_id="A", topic_category="manning"),
        rule(rule_id="Z", topic_category="fire_protection"),
    ]
    assert [r.rule_id for r in evaluate_rules(rules, profile())] == ["Z", "A", "B"]

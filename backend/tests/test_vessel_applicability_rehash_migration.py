"""Migration 0008: omstempling af hashes uden at hvidvaske manipulation.

Forløbet afprøver begge retninger på rigtige data:

1. Regler udkastes og godkendes på nyeste revision (kanonisk hash).
2. ``downgrade`` til 0007 omstempler dem til den rå hash.
3. Én godkendt regel manipuleres direkte i databasen.
4. ``upgrade`` omstempler de uændrede — og lader den manipulerede være.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command

from app.db.migrations_runner import alembic_config
from app.db.session import get_engine, get_session_factory
from tests.test_vessel_applicability_seed import SEED

PAX, CARGO, SAFETY = (d["rule"]["rule_id"] for d in SEED["drafts"])


def _seed(approve: set[str]) -> None:
    from app.services.vessel_applicability import (
        ApplicabilityRule,
        VesselApplicabilityService,
    )
    from app.services.vessel_applicability.cache import EvaluationCache

    with get_session_factory()() as session:
        service = VesselApplicabilityService(session, cache=EvaluationCache())
        for draft in SEED["drafts"]:
            service.save_draft(
                ApplicabilityRule(**draft["rule"]),
                source_text=draft["source_text"] + " \r\n",  # med kopieringsstøj
                drafted_by=draft["drafted_by"],
            )
        for rule_id in approve:
            service.approve(rule_id, reviewer_id="expert", signature="sig")


def _hashes() -> dict[str, tuple[str, str, str | None]]:
    with get_engine().connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT rule_id, source_text_hash, content_hash, approved_content_hash "
            "FROM vessel_applicability_rules"
        )).all()
    return {r[0]: tuple(r[1:]) for r in rows}


def _integrity() -> dict[str, str | None]:
    from sqlalchemy import select

    from app.models import VesselApplicabilityRule
    from app.services.vessel_applicability import integrity_problem

    with get_session_factory()() as session:
        rows = session.scalars(
            select(VesselApplicabilityRule).where(VesselApplicabilityRule.status == "APPROVED")
        ).all()
        return {row.rule_id: integrity_problem(row) for row in rows}


def test_omstempling_begge_veje_og_manipulation_forbliver_synlig(database_url):
    _seed(approve={PAX, SAFETY})
    current = _hashes()
    assert _integrity() == {PAX: None, SAFETY: None}

    # 2. Ned til den rå hash: alle tre rækker omstemples.
    command.downgrade(alembic_config(), "0007_vessel_applicability")
    raw = _hashes()
    for rule_id in (PAX, CARGO, SAFETY):
        assert raw[rule_id][0] != current[rule_id][0], "teksthashen burde være skiftet"
    assert raw[CARGO][2] is None  # udkast har ingen godkendt hash

    # 3. Manipulation efter godkendelse, uden om gennemgangen.
    with get_engine().begin() as conn:
        conn.execute(
            sa.text("UPDATE vessel_applicability_rules SET source_text = :t WHERE rule_id = :r"),
            {"t": "I skibe, hvor der er ansat mindst 50 personer ...", "r": SAFETY},
        )

    # 4. Op igen.
    command.upgrade(alembic_config(), "head")
    after = _hashes()
    assert after[PAX] == current[PAX], "uændret regel skal få præcis den kanoniske hash igen"
    assert after[CARGO][:2] == current[CARGO][:2]
    assert after[SAFETY] == raw[SAFETY], "manipuleret regel må ikke omstemples"

    integrity = _integrity()
    assert integrity[PAX] is None
    assert integrity[SAFETY] is not None

    with get_engine().connect() as conn:
        events = conn.execute(sa.text(
            "SELECT r.rule_id, e.actor, e.note FROM vessel_applicability_rule_events e "
            "JOIN vessel_applicability_rules r ON r.id = e.rule_pk "
            "WHERE e.event_type = 'REHASHED'"
        )).all()
    # PAX og CARGO: én gang ned, én gang op. SAFETY: kun ned.
    # Noter fra nedgraderingen forsvinder med kolonnen; opgraderingens står.
    by_rule, noted = {}, set()
    for rule_id, actor, note in events:
        by_rule[rule_id] = by_rule.get(rule_id, 0) + 1
        assert actor == "migration:0008"
        if note:
            assert "->" in note
            noted.add(rule_id)
    assert by_rule == {PAX: 2, CARGO: 2, SAFETY: 1}
    assert noted == {PAX, CARGO}


def test_upgrade_er_idempotent(database_url):
    _seed(approve={PAX})
    before = _hashes()
    command.downgrade(alembic_config(), "0007_vessel_applicability")
    command.upgrade(alembic_config(), "head")
    command.downgrade(alembic_config(), "0007_vessel_applicability")
    command.upgrade(alembic_config(), "head")
    assert _hashes() == before

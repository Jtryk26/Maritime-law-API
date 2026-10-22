"""Migration 0008: omstempling af hashes uden at hvidvaske manipulation.

Rækkerne skrives direkte på revision 0007 med den rå teksthash — sådan som
data så ud før 0008 — og migrationen køres derefter op og ned:

1. Uændrede rækker omstemples til den kanoniske hash (og tilbage igen).
2. En godkendt række, der er manipuleret direkte i databasen, røres ikke.
3. Hver omstempling efterlader en ``REHASHED``-hændelse.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import command

from app.db.migrations_runner import BACKEND_DIR, alembic_config
from app.db.session import get_engine

_spec = importlib.util.spec_from_file_location(
    "m0008", BACKEND_DIR / "migrations" / "versions" / "0008_vessel_rules_canonical_text_hash.py"
)
m0008 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0008)

TEXT = (
    "§ 4.\u00a0Stk. 1. I skibe, hvor der er ansat mindst 5 personer, skal \r\n"
    "sikkerhedsarbejdet organiseres."
)


class _Row:
    """Det, ``m0008.content_hash`` læser af en række."""

    def __init__(self, rule_id: str, status: str) -> None:
        self.rule_id = rule_id
        self.status = status
        self.legal_source_id = "BEK-2017-863"
        self.citation = "BEK nr. 863 af 27/06/2017 § 4, stk. 1"
        self.topic_category = "crew_and_certification"
        self.source_url = None
        self.inclusion_conditions = [
            {"field": "actual_crew_count", "operator": "gte", "target_value": 5}
        ]
        self.exclusion_conditions = []
        self.valid_from = self.valid_to = self.keel_laid_after = self.keel_laid_before = None


def _insert_raw(conn, rule_id: str, status: str) -> None:
    row = _Row(rule_id, status)
    text_hash = m0008.text_hash_v1(TEXT)
    digest = m0008.content_hash(row, text_hash)
    now = datetime.now(timezone.utc)
    conn.execute(
        sa.text(
            "INSERT INTO vessel_applicability_rules (rule_id, legal_source_id, citation, "
            "topic_category, status, inclusion_conditions, exclusion_conditions, source_text, "
            "source_text_hash, content_hash, drafted_by, approved_by, approved_at, "
            "approval_signature, approved_content_hash, created_at, updated_at) VALUES "
            "(:rule_id, :legal, :citation, :topic, :status, :inc, :exc, :text, :th, :ch, "
            "'generator:test@1', :by, :at, :sig, :ach, :now, :now)"
        ),
        {
            "rule_id": rule_id, "legal": row.legal_source_id, "citation": row.citation,
            "topic": row.topic_category, "status": status,
            "inc": json.dumps(row.inclusion_conditions), "exc": "[]", "text": TEXT,
            "th": text_hash, "ch": digest,
            "by": "jacob" if status == "APPROVED" else None,
            "at": now if status == "APPROVED" else None,
            "sig": "sig" if status == "APPROVED" else None,
            "ach": digest if status == "APPROVED" else None,
            "now": now,
        },
    )


def _hashes() -> dict[str, tuple]:
    with get_engine().connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT rule_id, source_text_hash, content_hash, approved_content_hash "
            "FROM vessel_applicability_rules"
        )).all()
    return {r[0]: tuple(r[1:]) for r in rows}


def _expected_v2(rule_id: str, status: str) -> tuple:
    text_hash = m0008.text_hash_v2(TEXT)
    digest = m0008.content_hash(_Row(rule_id, status), text_hash)
    return text_hash, digest, digest if status == "APPROVED" else None


def test_omstempling_og_manipulation_forbliver_synlig(database_url):
    command.downgrade(alembic_config(), "0007_vessel_applicability")
    with get_engine().begin() as conn:
        _insert_raw(conn, "APPROVED-OK", "APPROVED")
        _insert_raw(conn, "DRAFT-OK", "DRAFT")
        _insert_raw(conn, "APPROVED-TAMPERED", "APPROVED")
        conn.execute(sa.text(
            "UPDATE vessel_applicability_rules SET source_text = 'mindst 50 personer' "
            "WHERE rule_id = 'APPROVED-TAMPERED'"
        ))
    raw = _hashes()

    command.upgrade(alembic_config(), "head")
    after = _hashes()
    assert after["APPROVED-OK"] == _expected_v2("APPROVED-OK", "APPROVED")
    assert after["DRAFT-OK"] == _expected_v2("DRAFT-OK", "DRAFT")
    assert after["APPROVED-TAMPERED"] == raw["APPROVED-TAMPERED"], "må ikke omstemples"

    with get_engine().connect() as conn:
        events = conn.execute(sa.text(
            "SELECT r.rule_id, e.actor, e.note FROM vessel_applicability_rule_events e "
            "JOIN vessel_applicability_rules r ON r.id = e.rule_pk "
            "WHERE e.event_type = 'REHASHED'"
        )).all()
    assert sorted(r for r, _, _ in events) == ["APPROVED-OK", "DRAFT-OK"]
    assert all(actor == "migration:0008" and "->" in note for _, actor, note in events)


def test_omstempling_begge_veje_er_idempotent(database_url):
    command.downgrade(alembic_config(), "0007_vessel_applicability")
    with get_engine().begin() as conn:
        _insert_raw(conn, "APPROVED-OK", "APPROVED")
    raw = _hashes()

    command.upgrade(alembic_config(), "head")
    upgraded = _hashes()
    command.downgrade(alembic_config(), "0007_vessel_applicability")
    assert _hashes() == raw
    command.upgrade(alembic_config(), "head")
    command.upgrade(alembic_config(), "head")
    assert _hashes() == upgraded

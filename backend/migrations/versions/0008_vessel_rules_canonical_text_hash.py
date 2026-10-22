"""Vessel Applicability 2.0: lovtekstens hash beregnes over kanonisk tekst.

Fra og med denne revision hashes ``source_text`` over en kanonisk form (NFC,
ens linjeskift, normaliserede mellemrum) i stedet for over de rå bytes. Det
ændrer ``source_text_hash`` og dermed ``content_hash`` for enhver eksisterende
række — og for godkendte regler også ``approved_content_hash``.

**Hvorfor rækkerne ikke bare genberegnes.** En godkendelse attesterer, at
indholdet er det, anmelderen så. Genberegnede man blindt de nye hashes, ville
en række, der er rettet direkte i databasen efter godkendelsen, få et nyt,
gyldigt stempel — migrationen ville hvidvaske manipulationen. Derfor:

1. Rækken efterprøves først med den **gamle** metode. Kun hvis både
   tekst- og indholdshash stemmer (og for godkendte regler også den
   godkendte hash), er indholdet bevisligt uændret siden udkast/godkendelse.
2. Kun da omstemples den med den nye metode, og der skrives en
   ``REHASHED``-hændelse med begge hashes, så skiftet kan efterprøves.
3. Rækker, der allerede stemmer med den nye metode, springes over.
4. Rækker, der hverken stemmer med den gamle eller den nye metode, røres
   ikke. De var allerede kompromitterede og bliver ved med at give
   ``NEEDS_MANUAL_REVIEW``; antallet skrives i migrationens log.

Hashfunktionerne er **kopieret** hertil frem for importeret fra applikationen.
En migration skal give samme resultat om fem år, også hvis
`app.services.vessel_applicability` ændrer sig.

``downgrade`` er symmetrisk: den efterprøver med den nye metode og omstempler
med den gamle.

Revision ID: 0008_vessel_rules_canonical_text_hash
Revises: 0007_vessel_applicability
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import date, datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0008_vessel_rules_canonical_text_hash"
down_revision = "0007_vessel_applicability"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

ACTOR = "migration:0008"

# ---------------------------------------------------------------------------
# Frosne hashfunktioner
# ---------------------------------------------------------------------------

_SPACE_LIKE = re.compile(r"[   \t]")
_ZERO_WIDTH = re.compile(r"[​‌‍﻿]")
_BLANK_RUNS = re.compile(r"\n{3,}")


def _canonical_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH.sub("", _SPACE_LIKE.sub(" ", text))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()


def text_hash_v1(text: str | None) -> str | None:
    """Revision 0007: SHA-256 over de rå bytes."""
    return hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def text_hash_v2(text: str | None) -> str | None:
    """Revision 0008: SHA-256 over den kanoniske form."""
    if text is None:
        return None
    return hashlib.sha256(_canonical_text(text).encode()).hexdigest()


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):  # SQLite kan returnere datoer som tekst
        return date.fromisoformat(value[:10]).isoformat()
    return value.isoformat()


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def content_hash(row, source_text_hash: str | None) -> str:
    """Samme serialisering som ``ApplicabilityRule.model_dump(mode='json')``."""
    body = {
        "rule_id": row.rule_id,
        "legal_source_id": row.legal_source_id,
        "citation": row.citation,
        "topic_category": row.topic_category,
        "source_url": row.source_url,
        "inclusion_conditions": _json(row.inclusion_conditions),
        "exclusion_conditions": _json(row.exclusion_conditions) or [],
        "valid_from": _iso(row.valid_from),
        "valid_to": _iso(row.valid_to),
        "keel_laid_after": _iso(row.keel_laid_after),
        "keel_laid_before": _iso(row.keel_laid_before),
        "source_text_hash": source_text_hash,
    }
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Omstempling
# ---------------------------------------------------------------------------

_rules = sa.table(
    "vessel_applicability_rules",
    sa.column("id", sa.Integer),
    sa.column("rule_id", sa.String),
    sa.column("legal_source_id", sa.String),
    sa.column("citation", sa.String),
    sa.column("topic_category", sa.String),
    sa.column("status", sa.String),
    sa.column("source_url", sa.Text),
    sa.column("inclusion_conditions", sa.JSON),
    sa.column("exclusion_conditions", sa.JSON),
    sa.column("valid_from", sa.Date),
    sa.column("valid_to", sa.Date),
    sa.column("keel_laid_after", sa.Date),
    sa.column("keel_laid_before", sa.Date),
    sa.column("source_text", sa.Text),
    sa.column("source_text_hash", sa.String),
    sa.column("content_hash", sa.String),
    sa.column("approved_content_hash", sa.String),
)

_events = sa.table(
    "vessel_applicability_rule_events",
    sa.column("rule_pk", sa.Integer),
    sa.column("event_type", sa.String),
    sa.column("actor", sa.String),
    sa.column("content_hash", sa.String),
    sa.column("previous_status", sa.String),
    sa.column("note", sa.Text),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

HashFn = Callable[[str | None], str | None]


def _verifies(row, text_hash: HashFn) -> tuple[bool, str | None, str | None]:
    """(stemmer, teksthash, indholdshash) for rækken under en given metode."""
    t = text_hash(row.source_text)
    c = content_hash(row, t)
    ok = t == row.source_text_hash and c == row.content_hash
    if row.status == "APPROVED":
        ok = ok and c == row.approved_content_hash
    return ok, t, c


def rehash(bind, source: HashFn, target: HashFn, label: str) -> dict[str, int]:
    """Omstempl rækker, der bevisligt stemmer under ``source``, til ``target``.

    Returnerer optællingen, så den kan logges og afprøves.
    """
    counts = {"rehashed": 0, "already_current": 0, "unverifiable": 0}
    now = datetime.now(timezone.utc)
    for row in bind.execute(sa.select(_rules)).all():
        if _verifies(row, target)[0]:
            counts["already_current"] += 1
            continue
        ok, _, _ = _verifies(row, source)
        if not ok:
            counts["unverifiable"] += 1
            logger.warning("Regel %s stemmer ikke med nogen hashmetode; urørt.", row.rule_id)
            continue
        new_text = target(row.source_text)
        new_content = content_hash(row, new_text)
        values = {"source_text_hash": new_text, "content_hash": new_content}
        if row.status == "APPROVED":
            values["approved_content_hash"] = new_content
        bind.execute(sa.update(_rules).where(_rules.c.id == row.id).values(**values))
        bind.execute(sa.insert(_events).values(
            rule_pk=row.id,
            event_type="REHASHED",
            actor=ACTOR,
            content_hash=new_content,
            previous_status=row.status,
            note=f"{label}: indholdshash {row.content_hash} -> {new_content}; "
                 f"teksthash {row.source_text_hash} -> {new_text}. "
                 "Indholdet var uændret under den tidligere metode.",
            created_at=now,
        ))
        counts["rehashed"] += 1
    logger.info("vessel_applicability %s: %s", label, counts)
    return counts


def upgrade() -> None:
    op.add_column("vessel_applicability_rule_events", sa.Column("note", sa.Text(), nullable=True))
    rehash(op.get_bind(), text_hash_v1, text_hash_v2, "kanonisk teksthash (NFC)")


def downgrade() -> None:
    rehash(op.get_bind(), text_hash_v2, text_hash_v1, "tilbage til rå teksthash")
    with op.batch_alter_table("vessel_applicability_rule_events") as batch:
        batch.drop_column("note")

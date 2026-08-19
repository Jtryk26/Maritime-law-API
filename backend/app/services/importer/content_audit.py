"""Hvor meget af korpusset har vi faktisk lovteksten til?

Diagnostikken der udløste modulet
=================================
3.411 maritime dokumenter i produktionen, men kun 973 med paragraftegn.
Det ligner et parserproblem — og var det delvist — men kontrol mod kilden
viste, at en stor del af dokumenterne slet ikke HAR fuldtekst hos
Retsinformation. ELI-XML for fx ``A18650999930`` svarer HTTP 200 med et
``<Dokument>``, der kun rummer ``<Meta>``.

Så længe de to tilfælde blev talt sammen, kunne ingen afgøre, om
korpusset skulle repareres eller blot var, hvad kilden har.

Hvad modulet kan — og ikke kan
==============================
:func:`summarize_content_kinds` tæller fordelingen. :func:`reclassify`
genberegner ``documents.content_kind`` ud fra det, der allerede ligger i
databasen.

Grænsen er vigtig: ``metadata_only`` kan kun fastslås af **kilden**. Den
gamle parser skrev metadatateksten ind i indholdsfeltet, så en gammel
række ser ud som om den har brødtekst. Oplysningen findes kun for
dokumenter hentet med den rettede parser, hvor versionens metadata bærer
``source_had_body``. Rækker uden det flag tælles særskilt som "kræver
genhentning" frem for at blive gættet på plads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Document, DocumentVersion
from app.services.legal.content_kind import (
    CONTENT_KIND_METADATA_ONLY,
    CONTENT_KINDS,
    classify_content,
)

logger = get_logger(__name__)

__all__ = [
    "ContentKindSummary",
    "ReclassifyReport",
    "summarize_content_kinds",
    "reclassify",
    "UNSET_LABEL",
]

#: Vises for dokumenter, der endnu ikke har fået en vurdering.
UNSET_LABEL = "(ikke vurderet)"


@dataclass(slots=True)
class ContentKindSummary:
    """Fordelingen af indholdstyper, samlet og for det maritime udsnit."""

    total: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    maritime_total: int = 0
    maritime_counts: dict[str, int] = field(default_factory=dict)
    #: Dokumenter uden herkomstoplysning om kildens brødtekst. De kan
    #: ikke skelnes mellem ``metadata_only`` og tabt tekst uden en
    #: genhentning fra kilden.
    unverified: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "counts": self.counts,
            "maritime_total": self.maritime_total,
            "maritime_counts": self.maritime_counts,
            "unverified": self.unverified,
        }


@dataclass(slots=True)
class ReclassifyReport:
    """Resultatet af en genberegning."""

    examined: int = 0
    changed: int = 0
    unchanged: int = 0
    #: Fra-til, fx ``{("text_without_paragraph_sign", "full_text"): 12}``.
    transitions: dict[tuple[str, str], int] = field(default_factory=dict)
    dry_run: bool = False

    def record(self, old: str | None, new: str) -> None:
        self.examined += 1
        if old == new:
            self.unchanged += 1
            return
        self.changed += 1
        key = (old or UNSET_LABEL, new)
        self.transitions[key] = self.transitions.get(key, 0) + 1


def source_had_body(metadata_json: dict[str, Any] | None) -> bool | None:
    """Aflæser herkomstflaget fra en versions gemte kildemetadata.

    Returnerer ``None`` for rækker hentet før flaget fandtes. Det er
    ikke en fejl — det er netop den gruppe, der kræver genhentning.
    """
    metadata = metadata_json or {}
    source = metadata.get("source")
    if isinstance(source, dict) and isinstance(source.get("source_had_body"), bool):
        return source["source_had_body"]

    normalized = metadata.get("normalized")
    if isinstance(normalized, dict):
        kind = normalized.get("content_kind")
        if kind == CONTENT_KIND_METADATA_ONLY:
            return False
        if kind in CONTENT_KINDS:
            return True
    return None


def _current_version_rows(session: Session, *, maritime_only: bool, with_content: bool):
    """Ét opslag: dokument-id, nuværende vurdering og versionens data.

    Kun de nødvendige kolonner hentes. Hele korpusset skal kunne
    gennemløbes uden at læse hver lovtekst ind som ORM-objekt.
    """
    columns = [Document.id, Document.content_kind, DocumentVersion.metadata_json]
    if with_content:
        columns.append(DocumentVersion.content)

    query = select(*columns).join(
        DocumentVersion,
        Document.current_version_id == DocumentVersion.id,
        isouter=True,
    )
    if maritime_only:
        query = query.where(Document.is_maritime.is_(True))
    return session.execute(query).all()


def summarize_content_kinds(
    session: Session, *, maritime_only: bool = False
) -> ContentKindSummary:
    """Tæller fordelingen af ``documents.content_kind``."""
    summary = ContentKindSummary()

    rows = session.execute(
        select(
            Document.content_kind, Document.is_maritime, func.count(Document.id)
        ).group_by(Document.content_kind, Document.is_maritime)
    ).all()

    for kind, is_maritime, count in rows:
        label = kind or UNSET_LABEL
        if is_maritime:
            summary.maritime_total += count
            summary.maritime_counts[label] = summary.maritime_counts.get(label, 0) + count
        if maritime_only and not is_maritime:
            continue
        summary.total += count
        summary.counts[label] = summary.counts.get(label, 0) + count

    for _id, _kind, metadata_json in _current_version_rows(
        session, maritime_only=maritime_only, with_content=False
    ):
        if source_had_body(metadata_json) is None:
            summary.unverified += 1

    return summary


def reclassify(
    session: Session,
    *,
    maritime_only: bool = False,
    dry_run: bool = False,
) -> ReclassifyReport:
    """Genberegner ``content_kind`` ud fra den tekst, der allerede ligger.

    Idempotent, og rører aldrig ved selve teksten. Køres efter
    migrationen og igen efter enhver genimport.
    """
    report = ReclassifyReport(dry_run=dry_run)
    buckets: dict[str, list[int]] = {}

    for document_id, current_kind, metadata_json, content in _current_version_rows(
        session, maritime_only=maritime_only, with_content=True
    ):
        kind = classify_content(content, source_had_body=source_had_body(metadata_json))
        report.record(current_kind, kind)
        if kind != current_kind:
            buckets.setdefault(kind, []).append(document_id)

    if not dry_run:
        for kind, ids in buckets.items():
            # Skrives i portioner, så en stor genberegning ikke bygger
            # en IN-liste, databasen afviser.
            for start in range(0, len(ids), 500):
                session.execute(
                    update(Document)
                    .where(Document.id.in_(ids[start : start + 500]))
                    .values(content_kind=kind)
                )
        session.flush()

    logger.info(
        "content_audit.reclassified",
        extra={
            "examined": report.examined,
            "changed": report.changed,
            "dry_run": dry_run,
        },
    )
    return report

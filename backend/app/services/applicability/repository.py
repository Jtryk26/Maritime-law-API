"""Oversættelse mellem databasen og motorens domænegenstande.

Motoren kender ikke SQLAlchemy, og databasen kender ikke Kleene-logik. Denne
fil er det eneste sted, de to møder hinanden.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ApplicabilityCitation,
    ApplicabilityCondition,
    ApplicabilityCoverageGap,
    ApplicabilityDiscretion,
    ApplicabilityExclusion,
    ApplicabilityReviewEvent,
    ApplicabilityRule,
    Document,
    RuleReviewEventType,
    RuleReviewStatus,
)

from .drafting import RuleDraft
from .logic import Atom, AllOf, Always, AnyOf, Comparator, ConditionNode, NotNode, Strength, UnknownPolicy
from .rules import (
    ApplicabilityRuleSpec,
    CitationKind,
    CoverageGap,
    CoverageLevel,
    DiscretionClause,
    DiscretionEffect,
    ExclusionClause,
    ReviewStatus,
    RuleJurisdiction,
    RuleState,
    RuleStatus,
    ScopeCitation,
    ScopeCoverage,
)

__all__ = [
    "load_rule_specs",
    "load_rule_spec",
    "persist_draft",
    "record_review_event",
    "set_review_status",
]


# ---------------------------------------------------------------------------
# Læsning
# ---------------------------------------------------------------------------


def _node_from_rows(
    row: ApplicabilityCondition, children_by_parent: dict[int | None, list[ApplicabilityCondition]]
) -> ConditionNode:
    kids = sorted(children_by_parent.get(row.id, []), key=lambda r: r.position)
    if row.node_type == "atom":
        return Atom(
            id=f"c{row.id}",
            field_name=row.field_name or "",
            op=Comparator(row.op or "eq"),
            value=row.value_json,
            citation_key=row.citation_key or "",
            strength=Strength(row.strength or "hard"),
            tolerance=row.tolerance,
            unknown_policy=UnknownPolicy(row.unknown_policy or "unknown"),
            note=row.note,
        )
    if row.node_type == "all":
        return AllOf([_node_from_rows(kid, children_by_parent) for kid in kids])
    if row.node_type == "any":
        return AnyOf([_node_from_rows(kid, children_by_parent) for kid in kids])
    if row.node_type == "not":
        inner = _node_from_rows(kids[0], children_by_parent) if kids else None
        return NotNode(inner)
    return Always(bool(row.always_value), row.citation_key)


def load_rule_spec(rule: ApplicabilityRule, *, document: Document | None = None) -> ApplicabilityRuleSpec:
    """Bygger den genstand, motoren kan evaluere, ud af en gemt regel."""
    children_by_parent: dict[int | None, list[ApplicabilityCondition]] = {}
    for row in rule.conditions:
        children_by_parent.setdefault(row.parent_id, []).append(row)

    def roots(clause_kind: str, clause_id: str | None) -> list[ApplicabilityCondition]:
        return sorted(
            (
                row
                for row in children_by_parent.get(None, [])
                if row.clause_kind == clause_kind and row.clause_id == clause_id
            ),
            key=lambda r: r.position,
        )

    inclusion_roots = roots("inclusion", None)
    if not inclusion_roots:
        inclusion: ConditionNode | None = None
    elif len(inclusion_roots) == 1:
        inclusion = _node_from_rows(inclusion_roots[0], children_by_parent)
    else:
        inclusion = AllOf([_node_from_rows(r, children_by_parent) for r in inclusion_roots])

    exclusions: list[ExclusionClause] = []
    for clause in rule.exclusions:
        clause_roots = roots("exclusion", clause.clause_id)
        if not clause_roots:
            continue
        condition = (
            _node_from_rows(clause_roots[0], children_by_parent)
            if len(clause_roots) == 1
            else AllOf([_node_from_rows(r, children_by_parent) for r in clause_roots])
        )
        exclusions.append(
            ExclusionClause(
                clause_id=clause.clause_id,
                condition=condition,
                citation_key=clause.citation_key or "",
                label_da=clause.label,
            )
        )

    discretion: list[DiscretionClause] = []
    for clause in rule.discretion:
        clause_roots = roots("discretion", clause.clause_id)
        condition = (
            _node_from_rows(clause_roots[0], children_by_parent) if clause_roots else None
        )
        discretion.append(
            DiscretionClause(
                clause_id=clause.clause_id,
                authority=clause.authority,
                effect=DiscretionEffect(clause.effect),
                citation_key=clause.citation_key or "",
                condition=condition,
                label_da=clause.label,
            )
        )

    citations = {
        row.citation_key: ScopeCitation(
            key=row.citation_key,
            ref=row.ref,
            text=row.text,
            kind=CitationKind(row.kind),
            char_start=row.char_start,
            char_end=row.char_end,
            document_version_id=row.document_version_id,
            text_hash=row.text_hash,
        )
        for row in rule.citations
    }

    coverage = ScopeCoverage(
        level=CoverageLevel(rule.coverage_level),
        gaps=[
            CoverageGap(gap.citation_key, gap.reason)
            for gap in rule.coverage_gaps
            if not gap.resolved
        ],
        reviewed_by=rule.reviewed_by,
        reviewed_at=rule.reviewed_at.date() if rule.reviewed_at else None,
    )

    return ApplicabilityRuleSpec(
        rule_id=rule.id,
        document_id=rule.document_id,
        rule_ref=rule.rule_ref,
        title=rule.title,
        authority=rule.authority,
        document_type=rule.document_type,
        document_version_id=rule.document_version_id,
        source_url=document.source_url if document is not None else None,
        is_synthetic=bool(document.is_synthetic) if document is not None else False,
        status=RuleStatus(
            state=RuleState(rule.status_state),
            in_force_from=rule.in_force_from,
            in_force_to=rule.in_force_to,
            superseded_by_rule_id=rule.superseded_by_rule_id,
            citation_key=rule.status_citation_key,
        ),
        jurisdiction=RuleJurisdiction(
            flag_states=tuple(rule.flag_states or ["*"]),
            operating_areas=tuple(rule.operating_areas or ["*"]),
            port_state_applies=bool(rule.port_state_applies),
            citation_key=rule.jurisdiction_citation_key,
        ),
        inclusion=inclusion,
        exclusions=exclusions,
        discretion=discretion,
        citations=citations,
        coverage=coverage,
        review_status=ReviewStatus(rule.review_status),
        bindingness=rule.bindingness,
        speciality_boost=rule.speciality_boost,
    )


def load_rule_specs(
    session: Session,
    *,
    review_status: str | None = RuleReviewStatus.APPROVED.value,
    document_ids: list[int] | None = None,
    rule_ids: list[int] | None = None,
    limit: int | None = None,
) -> list[ApplicabilityRuleSpec]:
    """Henter regler klar til evaluering.

    Standard er ``review_status="approved"``: den offentlige vurdering ser kun
    regler, et menneske har sagt god for. ``None`` henter alt og er kun til
    administrativ forhåndsvisning.
    """
    stmt = (
        select(ApplicabilityRule, Document)
        .join(Document, Document.id == ApplicabilityRule.document_id)
        .options(
            selectinload(ApplicabilityRule.conditions),
            selectinload(ApplicabilityRule.citations),
            selectinload(ApplicabilityRule.exclusions),
            selectinload(ApplicabilityRule.discretion),
            selectinload(ApplicabilityRule.coverage_gaps),
        )
        .order_by(ApplicabilityRule.id)
    )
    if review_status is not None:
        stmt = stmt.where(ApplicabilityRule.review_status == review_status)
    if document_ids is not None:
        stmt = stmt.where(ApplicabilityRule.document_id.in_(document_ids))
    if rule_ids is not None:
        stmt = stmt.where(ApplicabilityRule.id.in_(rule_ids))
    if limit is not None:
        stmt = stmt.limit(limit)

    return [load_rule_spec(rule, document=document) for rule, document in session.execute(stmt)]


# ---------------------------------------------------------------------------
# Skrivning
# ---------------------------------------------------------------------------


def persist_draft(
    session: Session, draft: RuleDraft, *, draft_run_id: int | None = None
) -> ApplicabilityRule | None:
    """Gemmer et udkast. Findes det allerede for samme version, gøres intet.

    En ny dokumentversion giver et nyt udkast frem for at overskrive det
    gennemgåede — en godkendelse må aldrig følge med over på en tekst, ingen
    har set.
    """
    existing = session.scalar(
        select(ApplicabilityRule).where(
            ApplicabilityRule.document_id == draft.document_id,
            ApplicabilityRule.document_version_id == draft.document_version_id,
            ApplicabilityRule.rule_ref == draft.rule_ref,
        )
    )
    if existing is not None:
        return None

    rule = ApplicabilityRule(
        document_id=draft.document_id,
        document_version_id=draft.document_version_id,
        rule_ref=draft.rule_ref,
        title=draft.title,
        authority=draft.authority,
        document_type=draft.document_type,
        flag_states=["*"],
        operating_areas=["*"],
        port_state_applies=False,
        status_state=draft.status_state,
        in_force_from=draft.in_force_from,
        coverage_level=draft.coverage_level.value,
        review_status=RuleReviewStatus.DRAFT.value,
        origin="parser",
        draft_run_id=draft_run_id,
        bindingness=draft.bindingness,
    )
    session.add(rule)
    session.flush()

    for citation in draft.citations:
        session.add(
            ApplicabilityCitation(
                rule_id=rule.id,
                citation_key=citation.citation_key,
                ref=citation.ref,
                text=citation.text,
                kind=citation.kind.value,
                char_start=citation.char_start,
                char_end=citation.char_end,
                document_version_id=draft.document_version_id,
                text_hash=citation.text_hash,
            )
        )

    if draft.inclusion_atoms:
        root = ApplicabilityCondition(
            rule_id=rule.id, clause_kind="inclusion", clause_id=None, node_type="all", position=0
        )
        session.add(root)
        session.flush()
        for position, atom in enumerate(draft.inclusion_atoms):
            session.add(_condition_row(rule.id, root.id, "inclusion", None, position, atom))

    for clause in draft.exclusions:
        session.add(
            ApplicabilityExclusion(
                rule_id=rule.id,
                clause_id=clause.clause_id,
                citation_key=clause.citation_key,
                label=clause.label,
            )
        )
        root = ApplicabilityCondition(
            rule_id=rule.id,
            clause_kind="exclusion",
            clause_id=clause.clause_id,
            node_type="all",
            position=0,
        )
        session.add(root)
        session.flush()
        for position, atom in enumerate(clause.atoms):
            session.add(
                _condition_row(rule.id, root.id, "exclusion", clause.clause_id, position, atom)
            )

    for clause in draft.discretion:
        session.add(
            ApplicabilityDiscretion(
                rule_id=rule.id,
                clause_id=clause.clause_id,
                authority=clause.authority,
                effect=clause.effect.value,
                citation_key=clause.citation_key,
                label=clause.label,
            )
        )

    for citation_key, reason in draft.coverage_gaps:
        session.add(
            ApplicabilityCoverageGap(rule_id=rule.id, citation_key=citation_key, reason=reason)
        )

    record_review_event(
        session,
        rule,
        RuleReviewEventType.DRAFTED,
        actor="parser",
        note=f"Udkast dannet af skopparseren ({len(draft.inclusion_atoms)} betingelser).",
    )
    return rule


def _condition_row(
    rule_id: int, parent_id: int, clause_kind: str, clause_id: str | None, position: int, atom
) -> ApplicabilityCondition:
    return ApplicabilityCondition(
        rule_id=rule_id,
        parent_id=parent_id,
        clause_kind=clause_kind,
        clause_id=clause_id,
        position=position,
        node_type="atom",
        field_name=atom.field_name,
        op=atom.op.value,
        value_json=atom.value,
        citation_key=atom.citation_key,
        strength="hard",
        unknown_policy="unknown",
        note=atom.note,
        draft_confidence=atom.confidence,
    )


def record_review_event(
    session: Session,
    rule: ApplicabilityRule,
    event_type: RuleReviewEventType,
    *,
    actor: str | None = None,
    note: str | None = None,
) -> ApplicabilityReviewEvent:
    event = ApplicabilityReviewEvent(
        rule_id=rule.id,
        event_type=event_type.value,
        actor=actor,
        note=note,
        previous_status=rule.review_status,
        previous_coverage_level=rule.coverage_level,
    )
    session.add(event)
    return event


def set_review_status(
    session: Session,
    rule: ApplicabilityRule,
    status: RuleReviewStatus,
    *,
    actor: str | None = None,
    note: str | None = None,
    coverage_level: CoverageLevel | None = None,
) -> ApplicabilityRule:
    """Sætter gennemgangsstatus og skriver hændelsen i revisionssporet.

    ``coverage_level = complete`` kan kun sættes her — altså kun af et menneske.
    Parseren har ingen vej til den værdi.
    """
    event_type = {
        RuleReviewStatus.APPROVED: RuleReviewEventType.APPROVED,
        RuleReviewStatus.REJECTED: RuleReviewEventType.REJECTED,
        RuleReviewStatus.NEEDS_CHANGES: RuleReviewEventType.REOPENED,
        RuleReviewStatus.DRAFT: RuleReviewEventType.REOPENED,
    }[status]
    record_review_event(session, rule, event_type, actor=actor, note=note)

    rule.review_status = status.value
    rule.reviewed_by = actor
    rule.reviewed_at = datetime.now(timezone.utc)
    rule.review_note = note
    if coverage_level is not None:
        rule.coverage_level = coverage_level.value
    session.flush()
    return rule

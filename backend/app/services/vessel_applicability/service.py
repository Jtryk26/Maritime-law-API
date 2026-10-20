"""Database ↔ motor: udkast, godkendelse og vurdering.

Motoren (`engine.py`) er ren. Alt, der rører databasen eller cachen, ligger
her, så grænsen mellem "afgørelse" og "tilstand" står ét sted.

Tre tillidsgrænser håndhæves i dette modul:

1. **Kun APPROVED.** Vurderingen indlæser kun godkendte regler. Et udkast er
   ikke en regel.
2. **Godkendelsen er bundet til indholdet.** Ved godkendelse gemmes reglens
   indholdshash. Ved hver vurdering genberegnes hashen fra rækken; passer de
   ikke sammen (fx efter en direkte rettelse i databasen), afgør motoren
   ikke reglen, men svarer ``NEEDS_MANUAL_REVIEW``.
3. **En defekt regel er ikke en negativ regel.** Kan en godkendt række ikke
   læses som en gyldig regel, bliver den til ``NEEDS_MANUAL_REVIEW`` — den
   forsvinder ikke stille ud af rapporten.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import VesselApplicabilityRule, VesselApplicabilityRuleEvent

from .cache import EvaluationCache, evaluation_cache
from .engine import (
    ENGINE_VERSION,
    assumed_default_fields,
    evaluate_rule,
    supplied_fields,
    validate_condition,
)
from .models import (
    ApplicabilityRule,
    EvaluationReport,
    RuleEvaluationResult,
    RuleStatus,
    TopicGroup,
    TraceStep,
    Verdict,
    VesselProfile,
)

logger = get_logger(__name__)

__all__ = [
    "COVERAGE_NOTICE",
    "PLACEHOLDER_MARKER",
    "ApprovalOutcome",
    "InvalidRule",
    "RuleConflict",
    "RuleNotFound",
    "SeparationOfDutiesViolation",
    "VesselApplicabilityService",
    "canonicalize_legal_text",
    "content_hash",
    "integrity_problem",
    "profile_hash",
]

COVERAGE_NOTICE = (
    "Rapporten dækker kun regler, en faglig anmelder har godkendt. At en lov eller "
    "bestemmelse ikke optræder her, betyder IKKE, at den ikke gælder for skibet — "
    "kun at den endnu ikke er modelleret. Kontrollér altid den officielle tekst på "
    "Retsinformation ved juridisk anvendelse."
)


#: Markerer en kildetekst, der ikke er den ordrette lovtekst. Et udkast må
#: gerne bære den; en godkendelse må ikke. Ellers ville en pladsholder blive
#: hashet ind i revisionssporet som den tekst, anmelderen godkendte.
PLACEHOLDER_MARKER = "[PLADSHOLDER"


class RuleNotFound(LookupError):
    pass


class RuleConflict(RuntimeError):
    """Handlingen strider mod reglens nuværende tilstand."""


class SeparationOfDutiesViolation(PermissionError):
    """Den, der har skrevet udkastet, forsøger også at godkende det."""


class InvalidRule(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def content_hash(rule: ApplicabilityRule, source_text_hash: str | None) -> str:
    """Hash over alt, der kan ændre en afgørelse eller dens henvisning.

    ``status`` er bevidst udeladt: godkendelsen må ikke ændre det, der blev
    godkendt.
    """
    body = rule.model_dump(mode="json", exclude={"status"})
    body["source_text_hash"] = source_text_hash
    return _sha256(body)


def profile_hash(profile: VesselProfile) -> str:
    """Hash over præcis det, motoren ser.

    ``exclude_unset`` er ikke en optimering: et felt, brugeren har sat til
    sin standardværdi, er oplyst, mens et udeladt felt er ukendt. De to skal
    give forskellige hashes, fordi de giver forskellige afgørelser.
    """
    body = profile.model_dump(mode="json", exclude_unset=True)
    body["assessment_date"] = profile.assessment_date.isoformat()
    return _sha256(body)


#: Mellemrumstegn, der i praksis kommer ind via kopiering fra Retsinformation
#: og PDF'er og ikke bærer mening: hårde mellemrum, smalle hårde mellemrum,
#: tabulatorer og nulbredde-tegn.
_SPACE_LIKE = re.compile(r"[\u00a0\u2007\u202f\t]")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_BLANK_RUNS = re.compile(r"\n{3,}")


def canonicalize_legal_text(text: str) -> str:
    """Den form af lovteksten, hashen beregnes over.

    Fjerner forskelle, der opstår ved kopiering, men ikke ændrer teksten:
    linjeskift (CRLF/CR), hårde og nulbredde mellemrum, gentagne mellemrum,
    mellemrum i linjernes ender og gentagne tomme linjer.

    **NFC, ikke NFKC.** NFKC er en kompatibilitetsnormalisering og ændrer
    tegn, der kan bære betydning i en lovtekst: ``m²`` bliver ``m2``, ``½``
    bliver ``1⁄2``, og hævede fodnotehenvisninger forsvinder ind i teksten.
    NFC samler kun kanonisk ens tegn (fx ``a`` + kombinerende ring → ``å``).

    Tomme linjer bevares som én, fordi de adskiller stykker.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH.sub("", _SPACE_LIKE.sub(" ", text))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()


def _text_hash(text: str | None) -> str | None:
    """Hash over den kanoniske form. Selve teksten gemmes ordret, som indsendt."""
    if text is None:
        return None
    return hashlib.sha256(canonicalize_legal_text(text).encode()).hexdigest()


def integrity_problem(row: VesselApplicabilityRule) -> str | None:
    """Hvorfor en godkendt række ikke kan bruges — eller ``None``.

    Genberegner begge hashes fra rækkens nuværende indhold: kildeteksten
    *og* reglen. En rettelse direkte i databasen af enten betingelser eller
    lovtekst opdages derfor.
    """
    try:
        rule = _to_domain(row)
    except ValidationError as exc:
        return f"Den gemte regel kan ikke læses: {exc.error_count()} valideringsfejl."
    text_hash = _text_hash(row.source_text)
    if text_hash != row.source_text_hash:
        return "Den gemte lovtekst svarer ikke til den tekst, reglen blev godkendt sammen med."
    current = content_hash(rule, text_hash)
    if current != row.approved_content_hash:
        return (
            "Reglens indhold svarer ikke til det, der blev godkendt "
            f"(godkendt {row.approved_content_hash}, nu {current})."
        )
    return None


# ---------------------------------------------------------------------------
# Række ↔ domæne
# ---------------------------------------------------------------------------


def _to_domain(row: VesselApplicabilityRule) -> ApplicabilityRule:
    return ApplicabilityRule(
        rule_id=row.rule_id,
        legal_source_id=row.legal_source_id,
        citation=row.citation,
        topic_category=row.topic_category,
        status=RuleStatus(row.status),
        source_url=row.source_url,
        inclusion_conditions=row.inclusion_conditions,
        exclusion_conditions=row.exclusion_conditions or [],
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        keel_laid_after=row.keel_laid_after,
        keel_laid_before=row.keel_laid_before,
    )


def _untrusted_result(row: VesselApplicabilityRule, problem: str) -> RuleEvaluationResult:
    """Resultatet for en godkendt række, motoren ikke kan stole på."""
    return RuleEvaluationResult(
        rule_id=row.rule_id,
        citation=row.citation,
        legal_source_id=row.legal_source_id,
        topic_category=row.topic_category,
        verdict=Verdict.NEEDS_MANUAL_REVIEW,
        reasons=[problem, "Reglen er ikke afgjort. Vurder bestemmelsen manuelt."],
        missing_fields=[],
        source_url=row.source_url,
        steps=[TraceStep(step="temporal_validity", outcome="skipped", detail=problem)],
        conditions=[],
    )


def validate_rule(rule: ApplicabilityRule) -> list[str]:
    """Alle fejl, der ville gøre reglen uevaluerbar. Tom liste = i orden."""
    errors = []
    for label, kind, conditions in (
        ("inclusion_conditions", "inclusion", rule.inclusion_conditions),
        ("exclusion_conditions", "exclusion", rule.exclusion_conditions),
    ):
        for index, condition in enumerate(conditions):
            problem = validate_condition(condition, kind_label=kind)
            if problem:
                errors.append(f"{label}[{index}]: {problem}")
    return errors


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    row: VesselApplicabilityRule
    invalidated_evaluations: int


# ---------------------------------------------------------------------------
# Tjenesten
# ---------------------------------------------------------------------------


class VesselApplicabilityService:
    def __init__(self, session: Session, cache: EvaluationCache | None = None) -> None:
        self.session = session
        self.cache = cache if cache is not None else evaluation_cache

    # --- Vurdering -----------------------------------------------------------

    def ruleset_fingerprint(self) -> str:
        """Billigt opslag: to kolonner pr. godkendt regel."""
        rows = self.session.execute(
            select(VesselApplicabilityRule.rule_id, VesselApplicabilityRule.content_hash)
            .where(VesselApplicabilityRule.status == RuleStatus.APPROVED.value)
            .order_by(VesselApplicabilityRule.rule_id)
        ).all()
        return _sha256({"engine": ENGINE_VERSION, "rules": [[r, h] for r, h in rows]})

    def evaluate(self, profile: VesselProfile) -> EvaluationReport:
        fingerprint = self.ruleset_fingerprint()
        key = profile_hash(profile)
        hit = self.cache.get(fingerprint, key)
        if hit is not None:
            return hit.model_copy(update={"cached": True})

        rows = self.session.scalars(
            select(VesselApplicabilityRule).where(
                VesselApplicabilityRule.status == RuleStatus.APPROVED.value
            )
        ).all()

        results = [self._evaluate_row(row, profile) for row in rows]
        results.sort(key=lambda r: (r.topic_category, r.rule_id))

        report = _build_report(profile, results, fingerprint, key)
        self.cache.put(fingerprint, key, report)
        return report

    def _evaluate_row(
        self, row: VesselApplicabilityRule, profile: VesselProfile
    ) -> RuleEvaluationResult:
        problem = integrity_problem(row)
        if problem:
            logger.warning("vessel_applicability.rule_untrusted", extra={"rule_id": row.rule_id})
            return _untrusted_result(row, problem)
        return evaluate_rule(_to_domain(row), profile)

    def approved_rules(self) -> list[VesselApplicabilityRule]:
        return list(self.session.scalars(
            select(VesselApplicabilityRule)
            .where(VesselApplicabilityRule.status == RuleStatus.APPROVED.value)
            .order_by(VesselApplicabilityRule.topic_category, VesselApplicabilityRule.rule_id)
        ).all())

    # --- Udkast ---------------------------------------------------------------

    def save_draft(
        self,
        rule: ApplicabilityRule,
        *,
        source_text: str,
        drafted_by: str | None,
    ) -> VesselApplicabilityRule:
        """Gem eller erstat et udkast. Godkendte regler kan ikke overskrives."""
        errors = validate_rule(rule)
        if errors:
            raise InvalidRule(errors)

        rule = rule.model_copy(update={"status": RuleStatus.DRAFT})
        text_hash = _text_hash(source_text)
        digest = content_hash(rule, text_hash)

        row = self.session.scalar(
            select(VesselApplicabilityRule).where(VesselApplicabilityRule.rule_id == rule.rule_id)
        )
        if row is not None and row.status != RuleStatus.DRAFT.value:
            raise RuleConflict(
                f"Reglen '{rule.rule_id}' har status {row.status} og kan ikke overskrives "
                "af et udkast. Opret reglen under et nyt rule_id."
            )
        event_type = "REDRAFTED" if row is not None else "DRAFTED"
        if row is None:
            row = VesselApplicabilityRule(rule_id=rule.rule_id)
            self.session.add(row)

        row.legal_source_id = rule.legal_source_id
        row.citation = rule.citation
        row.topic_category = rule.topic_category
        row.status = RuleStatus.DRAFT.value
        row.source_url = rule.source_url
        row.inclusion_conditions = [c.model_dump(mode="json") for c in rule.inclusion_conditions]
        row.exclusion_conditions = [c.model_dump(mode="json") for c in rule.exclusion_conditions]
        row.valid_from = rule.valid_from
        row.valid_to = rule.valid_to
        row.keel_laid_after = rule.keel_laid_after
        row.keel_laid_before = rule.keel_laid_before
        row.source_text = source_text
        row.source_text_hash = text_hash
        row.content_hash = digest
        row.drafted_by = drafted_by
        self.session.flush()

        self.session.add(VesselApplicabilityRuleEvent(
            rule_pk=row.id,
            event_type=event_type,
            actor=drafted_by,
            content_hash=digest,
            previous_status=RuleStatus.DRAFT.value if event_type == "REDRAFTED" else None,
        ))
        self.session.commit()
        self.session.refresh(row)
        return row

    # --- Godkendelse ------------------------------------------------------------

    def approve(
        self,
        rule_id: str,
        *,
        reviewer_id: str,
        signature: str,
        expected_content_hash: str | None = None,
    ) -> ApprovalOutcome:
        row = self.session.scalar(
            select(VesselApplicabilityRule).where(VesselApplicabilityRule.rule_id == rule_id)
        )
        if row is None:
            raise RuleNotFound(rule_id)
        if row.status != RuleStatus.DRAFT.value:
            raise RuleConflict(f"Kun udkast kan godkendes; reglen har status {row.status}.")
        if expected_content_hash is not None and expected_content_hash != row.content_hash:
            raise RuleConflict(
                "Udkastet er ændret, siden det blev gennemgået "
                f"(gennemgået {expected_content_hash}, nu {row.content_hash})."
            )
        if row.source_text and PLACEHOLDER_MARKER in row.source_text:
            raise RuleConflict(
                "Kildeteksten er en pladsholder. Indsæt den ordrette lovtekst som nyt "
                "udkast, før reglen godkendes."
            )
        # reviewer_id og drafted_by stammer begge fra verificerede tokens (se
        # `app.core.security.require_reviewer`), ikke fra forespørgslernes krop.
        if row.drafted_by and row.drafted_by.casefold() == reviewer_id.casefold():
            raise SeparationOfDutiesViolation(
                "Adskillelse af hverv: den, der har udkastet reglen, kan ikke også godkende den."
            )

        # Genberegn frem for at stole på den gemte hash: godkendelsen skal
        # dække præcis det indhold, rækken har nu.
        try:
            rule = _to_domain(row)
        except ValidationError as exc:
            raise InvalidRule([f"Den gemte regel kan ikke læses: {exc}"]) from exc
        errors = validate_rule(rule)
        if errors:
            raise InvalidRule(errors)
        if _text_hash(row.source_text) != row.source_text_hash:
            raise RuleConflict("Den gemte lovtekst svarer ikke til dens hash.")
        digest = content_hash(rule, row.source_text_hash)
        if digest != row.content_hash:
            raise RuleConflict("Reglens gemte hash svarer ikke til dens indhold.")

        previous = row.status
        row.status = RuleStatus.APPROVED.value
        row.approved_by = reviewer_id
        row.approved_at = utcnow()
        row.approval_signature = signature
        row.approved_content_hash = digest
        self.session.add(VesselApplicabilityRuleEvent(
            rule_pk=row.id,
            event_type="APPROVED",
            actor=reviewer_id,
            signature=signature,
            content_hash=digest,
            previous_status=previous,
        ))
        self.session.commit()
        self.session.refresh(row)

        invalidated = self.cache.invalidate_all()
        logger.info(
            "vessel_applicability.rule_approved",
            extra={"rule_id": rule_id, "reviewer": reviewer_id, "invalidated": invalidated},
        )
        return ApprovalOutcome(row=row, invalidated_evaluations=invalidated)


def _build_report(
    profile: VesselProfile,
    results: list[RuleEvaluationResult],
    fingerprint: str,
    key: str,
) -> EvaluationReport:
    def counts(items: list[RuleEvaluationResult]) -> dict[str, int]:
        tally = Counter(r.verdict.value for r in items)
        return {verdict.value: tally.get(verdict.value, 0) for verdict in Verdict}

    grouped: dict[str, list[RuleEvaluationResult]] = {}
    for result in results:
        grouped.setdefault(result.topic_category, []).append(result)

    return EvaluationReport(
        vessel_name=profile.vessel_name,
        imo_number=profile.imo_number,
        assessment_date=profile.assessment_date,
        engine_version=ENGINE_VERSION,
        ruleset_fingerprint=fingerprint,
        profile_hash=key,
        supplied_fields=sorted(supplied_fields(profile)),
        assumed_defaults=sorted(assumed_default_fields(profile)),
        rules_evaluated=len(results),
        counts=counts(results),
        topics=[
            TopicGroup(topic_category=topic, counts=counts(items), results=items)
            for topic, items in sorted(grouped.items())
        ],
        coverage_notice=COVERAGE_NOTICE,
    )

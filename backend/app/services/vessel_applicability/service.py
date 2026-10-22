"""Database ↔ motor: udkast, godkendelse, udfasning og vurdering.

Motoren (`engine.py`) er ren. Alt, der rører databasen eller cachen, ligger
her, så grænsen mellem "afgørelse" og "tilstand" står ét sted.

**Ingen skrivende metode committer.** Kalderen — ``python -m app.cli
vessel-rules`` — afgrænser transaktionen, så en hel batch enten lykkes
eller efterlader databasen uændret.

Fire tillidsgrænser håndhæves i dette modul:

1. **Kun APPROVED.** Vurderingen indlæser kun godkendte regler. Et udkast er
   ikke en regel.
2. **Godkendelsen er bundet til indholdet.** Ved godkendelse gemmes reglens
   indholdshash. Ved hver vurdering genberegnes hashen fra rækken; passer de
   ikke sammen (fx efter en direkte rettelse i databasen), afgør motoren
   ikke reglen, men svarer ``NEEDS_MANUAL_REVIEW``.
3. **En defekt regel er ikke en negativ regel.** Kan en godkendt række ikke
   læses som en gyldig regel, bliver den til ``NEEDS_MANUAL_REVIEW`` — den
   forsvinder ikke stille ud af rapporten.
4. **Lovteksten er efterprøvet, ikke påstået.** En regels ``source_text``
   skal findes ordret (efter kanonisering) i en høstet dokumentversion, før
   den kan udkastes eller godkendes. Der findes ingen omgåelse: mangler
   dokumentet, høstes det først. Versionen indgår i indholdshashen.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import (
    Document,
    DocumentVersion,
    VesselApplicabilityRule,
    VesselApplicabilityRuleEvent,
)

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
    "MIN_SOURCE_TEXT_CHARS",
    "DraftPlan",
    "InvalidRule",
    "RuleConflict",
    "RuleNotFound",
    "SeparationOfDutiesViolation",
    "SourceRef",
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


#: Korteste kildetekst, der accepteres. Ordret match af et par ord i et
#: langt dokument beviser intet; en sætning gør.
MIN_SOURCE_TEXT_CHARS = 40

#: Præfiks for maskinelle udkastere. En generator kan aldrig godkende.
GENERATOR_PREFIX = "generator:"


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


def content_hash(
    rule: ApplicabilityRule,
    source_text_hash: str | None,
    source_binding: dict[str, Any] | None = None,
) -> str:
    """Hash over alt, der kan ændre en afgørelse eller dens henvisning.

    ``status`` er bevidst udeladt: godkendelsen må ikke ændre det, der blev
    godkendt. Kildeversionen (`source_binding`) indgår kun, når den er sat,
    så hashes fra før migration 0009 er uændrede.
    """
    body = rule.model_dump(mode="json", exclude={"status"})
    body["source_text_hash"] = source_text_hash
    if source_binding is not None:
        body["source_document"] = source_binding
    return _sha256(body)


def source_binding(version: DocumentVersion) -> dict[str, Any]:
    """Kildeversionens identitet i portabel form — ikke databasens id'er."""
    return {
        "source": version.document.source,
        "source_id": version.document.source_id,
        "version_number": version.version_number,
        "content_hash": version.content_hash,
    }


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


def _match_form(text: str) -> str:
    """Kanonisk tekst med al whitespace — også linjeskift — som ét mellemrum.

    Retsinformation ombryder linjer anderledes end et udklip; ordfølgen er
    det, der skal stemme.
    """
    return " ".join(canonicalize_legal_text(text).split())


def source_text_problem(source_text: str, version: DocumentVersion) -> str | None:
    """Hvorfor kildeteksten ikke er efterprøvet mod versionen — eller ``None``."""
    needle = _match_form(source_text)
    if len(needle) < MIN_SOURCE_TEXT_CHARS:
        return (
            f"Kildeteksten er for kort til at efterprøve ({len(needle)} tegn; "
            f"mindst {MIN_SOURCE_TEXT_CHARS}). Citér hele bestemmelsen."
        )
    if needle not in _match_form(version.content or ""):
        doc = version.document
        return (
            f"Kildeteksten findes ikke ordret i {doc.source}:{doc.source_id} "
            f"version {version.version_number}. Ret teksten, eller høst den version, "
            "den stammer fra."
        )
    return None


def _row_problem(
    row: VesselApplicabilityRule, expected_hash: str | None, *, deep: bool
) -> str | None:
    """Fælles integritetskontrol for udkast (før godkendelse) og godkendte regler."""
    try:
        rule = _to_domain(row)
    except ValidationError as exc:
        return f"Den gemte regel kan ikke læses: {exc.error_count()} valideringsfejl."
    text_hash = _text_hash(row.source_text)
    if text_hash != row.source_text_hash:
        return "Den gemte lovtekst svarer ikke til den tekst, reglen blev udkastet med."
    version = row.document_version
    if version is None:
        return (
            "Reglen er ikke efterprøvet mod et høstet kildedokument. Udkast den igen "
            "med 'python -m app.cli vessel-rules ingest'."
        )
    current = content_hash(rule, text_hash, source_binding(version))
    if current != expected_hash:
        return (
            "Reglens indhold svarer ikke til det, der blev godkendt "
            f"(godkendt {expected_hash}, nu {current})."
        )
    if deep:
        return source_text_problem(row.source_text or "", version)
    return None


def integrity_problem(row: VesselApplicabilityRule, *, deep: bool = False) -> str | None:
    """Hvorfor en godkendt række ikke kan bruges — eller ``None``.

    Genberegner hashes fra rækkens nuværende indhold: lovtekst, regel og
    kildeversion. En rettelse direkte i databasen opdages derfor. ``deep``
    efterprøver desuden teksten ordret mod kildeversionen; det koster en
    søgning i hele dokumentet og bruges af ``vessel-rules verify``, ikke ved
    hver vurdering.
    """
    return _row_problem(row, row.approved_content_hash, deep=deep)


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
class SourceRef:
    """Peger på en høstet dokumentversion. ``version_number=None`` = aktuel."""

    source_id: str
    source: str = "retsinformation"
    version_number: int | None = None

    def __str__(self) -> str:
        version = "aktuel" if self.version_number is None else f"v{self.version_number}"
        return f"{self.source}:{self.source_id} ({version})"


@dataclass(frozen=True, slots=True)
class DraftPlan:
    """Hvad et udkast vil gøre — beregnet uden at skrive.

    ``outcome`` er ``created``, ``redrafted``, ``unchanged``, ``conflict``
    eller ``invalid``.
    """

    rule_id: str
    outcome: str
    content_hash: str | None
    errors: tuple[str, ...] = ()
    version: DocumentVersion | None = None
    #: Forhold, der ikke blokerer, men skal ses af et menneske — fx at
    #: kildedokumentet ikke længere er gældende.
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.outcome in {"created", "redrafted", "unchanged"}


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
        return self.rules(RuleStatus.APPROVED)

    def rules(self, status: RuleStatus | None = None) -> list[VesselApplicabilityRule]:
        query = select(VesselApplicabilityRule).order_by(
            VesselApplicabilityRule.topic_category, VesselApplicabilityRule.rule_id
        )
        if status is not None:
            query = query.where(VesselApplicabilityRule.status == status.value)
        return list(self.session.scalars(query).all())

    def get(self, rule_id: str) -> VesselApplicabilityRule:
        row = self.session.scalar(
            select(VesselApplicabilityRule).where(VesselApplicabilityRule.rule_id == rule_id)
        )
        if row is None:
            raise RuleNotFound(rule_id)
        return row

    # --- Kildedokumenter ----------------------------------------------------------

    def resolve_source(self, ref: SourceRef) -> DocumentVersion:
        """Den høstede version, ``ref`` peger på. Findes den ikke, er svaret nej."""
        document = self.session.scalar(
            select(Document).where(
                Document.source == ref.source, Document.source_id == ref.source_id
            )
        )
        if document is None:
            raise InvalidRule([
                f"Kildedokumentet {ref.source}:{ref.source_id} er ikke høstet. "
                "Høst det først; en regel kan ikke udkastes mod et dokument, "
                "systemet ikke har."
            ])
        if ref.version_number is None:
            version = (
                self.session.get(DocumentVersion, document.current_version_id)
                if document.current_version_id
                else None
            )
        else:
            version = self.session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.version_number == ref.version_number,
                )
            )
        if version is None:
            raise InvalidRule([f"Kildedokumentet {ref} har ingen sådan version."])
        from app.services.rights import RightsService
        if RightsService(self.session).source(document.source) is not None:
            raise InvalidRule(["International source rules require a separate reviewed integration."])
        return version

    # --- Udkast ---------------------------------------------------------------

    def plan_draft(
        self, rule: ApplicabilityRule, *, source_text: str, source: SourceRef
    ) -> DraftPlan:
        """Efterprøv et udkast uden at skrive noget."""
        plan = self._plan_draft(rule, source_text=source_text, source=source)
        if plan.version is None:
            return plan
        return replace(plan, warnings=source_status_warnings(plan.version, rule))

    def _plan_draft(
        self, rule: ApplicabilityRule, *, source_text: str, source: SourceRef
    ) -> DraftPlan:
        rule = rule.model_copy(update={"status": RuleStatus.DRAFT})
        errors = validate_rule(rule)
        version = None
        try:
            version = self.resolve_source(source)
        except InvalidRule as exc:
            errors.extend(exc.errors)
        if version is not None:
            problem = source_text_problem(source_text, version)
            if problem:
                errors.append(problem)
        if errors:
            return DraftPlan(rule.rule_id, "invalid", None, tuple(errors), version)

        digest = content_hash(rule, _text_hash(source_text), source_binding(version))
        try:
            row = self.get(rule.rule_id)
        except RuleNotFound:
            return DraftPlan(rule.rule_id, "created", digest, version=version)
        if row.status != RuleStatus.DRAFT.value:
            return DraftPlan(
                rule.rule_id,
                "conflict",
                digest,
                (
                    f"Reglen har status {row.status} og kan ikke overskrives af et udkast. "
                    "Udfas den, og udkast ændringen under et nyt rule_id.",
                ),
                version,
            )
        outcome = "unchanged" if row.content_hash == digest else "redrafted"
        return DraftPlan(rule.rule_id, outcome, digest, version=version)

    def save_draft(
        self,
        rule: ApplicabilityRule,
        *,
        source_text: str,
        source: SourceRef,
        drafted_by: str,
        note: str | None = None,
    ) -> DraftPlan:
        """Gem eller erstat et udkast. Committer ikke."""
        plan = self.plan_draft(rule, source_text=source_text, source=source)
        if plan.outcome == "invalid":
            raise InvalidRule(list(plan.errors))
        if plan.outcome == "conflict":
            raise RuleConflict(plan.errors[0])
        if plan.outcome == "unchanged":
            return plan

        rule = rule.model_copy(update={"status": RuleStatus.DRAFT})
        if plan.outcome == "created":
            row = VesselApplicabilityRule(rule_id=rule.rule_id)
            self.session.add(row)
        else:
            row = self.get(rule.rule_id)

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
        row.source_text_hash = _text_hash(source_text)
        row.document_version = plan.version
        row.content_hash = plan.content_hash
        row.drafted_by = drafted_by
        self.session.flush()

        self.session.add(VesselApplicabilityRuleEvent(
            rule_pk=row.id,
            event_type="DRAFTED" if plan.outcome == "created" else "REDRAFTED",
            actor=drafted_by,
            content_hash=plan.content_hash,
            previous_status=None if plan.outcome == "created" else RuleStatus.DRAFT.value,
            note=note,
        ))
        self.session.flush()
        return plan

    # --- Godkendelse og udfasning --------------------------------------------------

    def approve(
        self,
        rule_id: str,
        *,
        reviewer_id: str,
        signature: str,
        expected_content_hash: str,
    ) -> VesselApplicabilityRule:
        """Godkend præcis det indhold, anmelderen har læst. Committer ikke."""
        _check_reviewer(reviewer_id)
        row = self.get(rule_id)
        if row.status != RuleStatus.DRAFT.value:
            raise RuleConflict(f"Kun udkast kan godkendes; reglen har status {row.status}.")
        if expected_content_hash != row.content_hash:
            raise RuleConflict(
                "Udkastet er ændret, siden det blev gennemgået "
                f"(gennemgået {expected_content_hash}, nu {row.content_hash})."
            )
        if row.drafted_by and row.drafted_by.casefold() == reviewer_id.casefold():
            raise SeparationOfDutiesViolation(
                "Adskillelse af hverv: den, der har udkastet reglen, kan ikke også godkende den."
            )
        try:
            errors = validate_rule(_to_domain(row))
        except ValidationError as exc:
            errors = [f"Den gemte regel kan ikke læses: {exc.error_count()} valideringsfejl."]
        if errors:
            raise InvalidRule(errors)
        # Genberegn og efterprøv mod kilden igen: godkendelsen skal dække det
        # indhold, rækken har nu — ikke det, den havde ved udkastet.
        problem = _row_problem(row, row.content_hash, deep=True)
        if problem:
            raise RuleConflict(problem)

        row.status = RuleStatus.APPROVED.value
        row.approved_by = reviewer_id
        row.approved_at = utcnow()
        row.approval_signature = signature
        row.approved_content_hash = row.content_hash
        self.session.add(VesselApplicabilityRuleEvent(
            rule_pk=row.id,
            event_type="APPROVED",
            actor=reviewer_id,
            signature=signature,
            content_hash=row.content_hash,
            previous_status=RuleStatus.DRAFT.value,
        ))
        self.session.flush()
        self.cache.invalidate_all()
        return row

    def deprecate(self, rule_id: str, *, reviewer_id: str, reason: str) -> VesselApplicabilityRule:
        """Tag en regel ud af brug. Rækken og dens spor bevares. Committer ikke."""
        _check_reviewer(reviewer_id)
        row = self.get(rule_id)
        if row.status == RuleStatus.DEPRECATED.value:
            raise RuleConflict("Reglen er allerede udfaset.")
        previous = row.status
        row.status = RuleStatus.DEPRECATED.value
        self.session.add(VesselApplicabilityRuleEvent(
            rule_pk=row.id,
            event_type="DEPRECATED",
            actor=reviewer_id,
            content_hash=row.content_hash,
            previous_status=previous,
            note=reason,
        ))
        self.session.flush()
        self.cache.invalidate_all()
        return row


#: Retsinformations status for en gældende retsakt.
IN_FORCE_STATUS = "gældende"


def source_status_warnings(version: DocumentVersion, rule: ApplicabilityRule) -> tuple[str, ...]:
    """Advarsel — ikke afvisning — når kildedokumentet ikke er gældende.

    Historiske regler er legitime: en vurdering pr. en dato i 2017 skal bruge
    den lov, der gjaldt da. Men en regel mod en ophævet retsakt uden
    ``valid_to`` vil blive anvendt på dagsdato, og det skal et menneske se,
    før reglen godkendes.
    """
    status = (version.document.status or "").strip()
    if not status or status.casefold() == IN_FORCE_STATUS:
        return ()
    doc = version.document
    message = f"Kildedokumentet {doc.source}:{doc.source_id} har status '{status}'."
    if rule.valid_to is None:
        message += (
            " Reglen har ingen valid_to og vil derfor blive anvendt på dagsdato. "
            "Sæt valid_to, hvis den kun skal gælde historisk."
        )
    return (message,)


def _check_reviewer(reviewer_id: str) -> None:
    if not reviewer_id or not reviewer_id.strip():
        raise SeparationOfDutiesViolation("En anmelder skal have et navn.")
    if reviewer_id.casefold().startswith(GENERATOR_PREFIX):
        raise SeparationOfDutiesViolation(
            "En maskinel udkaster kan ikke godkende eller udfase regler; det kræver et menneske."
        )


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

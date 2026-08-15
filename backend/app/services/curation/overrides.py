"""Kuraterede relevans-overrides — den menneskelige rettelseskanal.

Den automatiske relevansmotor (``app.services.relevance``) er og
forbliver den primære klassifikation. Dette modul håndterer den
undtagelse, der uundgåeligt opstår, når en dokumentkontrol viser, at
motoren tog fejl for et *bestemt* accessionsnummer: en for lav
fuldtekstscore på et reelt maritimt dokument, eller omvendt et
dokument, der scorer højt på nøgleord uden reelt at være maritimt.

Princippet er det samme, som gælder resten af systemet: motoren må
ALDRIG få sin egen udregning ændret bagefter. En override tilføjer i
stedet en selvstændig, revisionssikker post — hvem/hvad besluttede det,
hvornår, med hvilken begrundelse — som importeren og repositoriet slår
op og anvender som den *effektive* afgørelse. Se
``app.services.importer.repository.DocumentRepository.store`` for hvor
den effektive afgørelse beregnes, og
``app.services.importer.service.ImportService._process_one`` for hvor
den slås op.

To tabeller, to formål
======================
:class:`~app.models.CuratedRelevanceOverride` bærer den AKTUELLE
afgørelse og overskrives ved ændring — det er den, importeren slår op.

:class:`~app.models.CuratedRelevanceOverrideEvent` er **append-only**
historik. Hver mutation herunder skriver præcis én uforanderlig række
med både forrige og ny tilstand. Uden den ville en rettet begrundelse
eller en fjernet override ikke efterlade noget spor, og "revisionssikker"
ville være en tom påstand. Der findes derfor med vilje ingen
opdaterings- eller sletteoperation for historikken i dette modul, og
der bør heller aldrig tilføjes en.

En mutation, der ikke ændrer noget (samme afgørelse, samme begrundelse,
samme tag), skriver INGEN historikpost. Ellers ville en idempotent
gentagelse af den samme CLI-kommando — hvilket er en normal og ønsket
arbejdsgang — fylde historikken med indholdsløse rækker.

Dette modul kender hverken importeren, relevansmotoren eller
efterindlæsningskøen — det ejer udelukkende de to override-tabeller.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    CuratedDecision,
    CuratedOverrideEventType,
    CuratedRelevanceOverride,
    CuratedRelevanceOverrideEvent,
)

logger = get_logger(__name__)

__all__ = [
    "InvalidDecisionError",
    "bulk_set_overrides",
    "clear_override",
    "get_override",
    "get_overrides",
    "list_overrides",
    "override_history",
    "set_override",
]

_VALID_DECISIONS = frozenset(CuratedDecision.values())


class InvalidDecisionError(ValueError):
    """Rejst når decision eller reason ikke opfylder minimumskravene."""


def _normalize_decision(decision: str) -> str:
    value = str(decision).strip().lower()
    if value not in _VALID_DECISIONS:
        raise InvalidDecisionError(
            f"Ugyldig decision {decision!r}. Gyldige værdier: "
            f"{', '.join(sorted(_VALID_DECISIONS))}."
        )
    return value


def _normalize_reason(reason: str) -> str:
    value = (reason or "").strip()
    if not value:
        raise InvalidDecisionError(
            "reason må ikke være tom — en override skal kunne begrundes."
        )
    return value


# ---------------------------------------------------------------------------
# Opslag
# ---------------------------------------------------------------------------


def get_override(session: Session, accession_number: str) -> CuratedRelevanceOverride | None:
    """Den kuraterede afgørelse for ét accessionsnummer, hvis den findes."""
    return session.get(CuratedRelevanceOverride, str(accession_number).strip())


def get_overrides(
    session: Session, accession_numbers: Iterable[str]
) -> dict[str, CuratedRelevanceOverride]:
    """Samme som :func:`get_override`, for flere accessionsnumre på én gang."""
    ids = [str(a).strip() for a in accession_numbers if a is not None and str(a).strip()]
    if not ids:
        return {}
    rows = session.scalars(
        select(CuratedRelevanceOverride).where(
            CuratedRelevanceOverride.accession_number.in_(ids)
        )
    ).all()
    return {row.accession_number: row for row in rows}


def list_overrides(
    session: Session,
    *,
    decision: str | None = None,
    source_tag: str | None = None,
) -> list[CuratedRelevanceOverride]:
    """Alle AKTUELLE overrides, valgfrit filtreret. Til admin-/CLI-visning."""
    stmt = select(CuratedRelevanceOverride).order_by(
        CuratedRelevanceOverride.accession_number
    )
    if decision is not None:
        stmt = stmt.where(CuratedRelevanceOverride.decision == _normalize_decision(decision))
    if source_tag is not None:
        stmt = stmt.where(CuratedRelevanceOverride.source_tag == source_tag)
    return list(session.scalars(stmt).all())


def override_history(
    session: Session, accession_number: str | None = None
) -> list[CuratedRelevanceOverrideEvent]:
    """Historikken i tidsrækkefølge, ældste først.

    Uden `accession_number` returneres hele historikken. Poster findes
    også for accessionsnumre, hvis override siden er fjernet — det er
    netop pointen med en append-only historik.
    """
    stmt = select(CuratedRelevanceOverrideEvent).order_by(
        CuratedRelevanceOverrideEvent.created_at,
        CuratedRelevanceOverrideEvent.id,
    )
    if accession_number is not None:
        stmt = stmt.where(
            CuratedRelevanceOverrideEvent.accession_number == str(accession_number).strip()
        )
    return list(session.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Historikskrivning (append-only)
# ---------------------------------------------------------------------------


def _record_event(
    session: Session,
    *,
    accession_number: str,
    event_type: CuratedOverrideEventType,
    previous: CuratedRelevanceOverride | None,
    new_decision: str | None,
    new_reason: str | None,
    new_source_tag: str | None,
    decision_source: str | None,
    decided_by: str | None,
) -> CuratedRelevanceOverrideEvent:
    """Tilføjer én uforanderlig historikpost.

    `previous` skal læses FØR den aktuelle override muteres — ellers
    registreres den nye tilstand som både forrige og ny.
    """
    event = CuratedRelevanceOverrideEvent(
        accession_number=accession_number,
        event_type=event_type.value,
        previous_decision=previous.decision if previous is not None else None,
        new_decision=new_decision,
        previous_reason=previous.reason if previous is not None else None,
        new_reason=new_reason,
        previous_source_tag=previous.source_tag if previous is not None else None,
        new_source_tag=new_source_tag,
        decision_source=decision_source,
        decided_by=decided_by,
    )
    session.add(event)
    return event


def _classify_change(
    previous: CuratedRelevanceOverride | None,
    *,
    decision: str,
    reason: str,
    source_tag: str,
    decided_by: str | None,
) -> CuratedOverrideEventType | None:
    """Hvilken hændelse en påtænkt skrivning svarer til.

    None betyder "intet ændrer sig" — så skrives ingen historikpost.
    """
    if previous is None:
        return CuratedOverrideEventType.CREATED
    if previous.decision != decision:
        return CuratedOverrideEventType.DECISION_CHANGED
    if (
        previous.reason != reason
        or previous.source_tag != source_tag
        or previous.decided_by != decided_by
    ):
        return CuratedOverrideEventType.DETAILS_UPDATED
    return None


# ---------------------------------------------------------------------------
# Skrivning
# ---------------------------------------------------------------------------


def set_override(
    session: Session,
    accession_number: str,
    decision: str,
    *,
    reason: str,
    source_tag: str,
    decided_by: str | None = None,
    decision_source: str = "curated",
) -> CuratedRelevanceOverride:
    """Opretter eller opdaterer den kuraterede afgørelse for ét dokument.

    Idempotent: kaldes den igen med præcis samme afgørelse, begrundelse,
    tag og ophavsmand, ændres intet, og der skrives ingen historikpost.
    Ændrer noget sig, opdateres den aktuelle post OG der tilføjes en
    uforanderlig historikpost med både den forrige og den nye tilstand.
    """
    accn = str(accession_number).strip()
    if not accn:
        raise InvalidDecisionError("accession_number må ikke være tomt.")
    normalized_decision = _normalize_decision(decision)
    normalized_reason = _normalize_reason(reason)

    existing = get_override(session, accn)
    event_type = _classify_change(
        existing,
        decision=normalized_decision,
        reason=normalized_reason,
        source_tag=source_tag,
        decided_by=decided_by,
    )

    if event_type is not None:
        # Historikken skal se den GAMLE tilstand, så den skrives før
        # felterne på `existing` overskrives nedenfor.
        _record_event(
            session,
            accession_number=accn,
            event_type=event_type,
            previous=existing,
            new_decision=normalized_decision,
            new_reason=normalized_reason,
            new_source_tag=source_tag,
            decision_source=decision_source,
            decided_by=decided_by,
        )

    if existing is None:
        existing = CuratedRelevanceOverride(accession_number=accn)
        session.add(existing)

    existing.decision = normalized_decision
    existing.decision_source = decision_source
    existing.reason = normalized_reason
    existing.source_tag = source_tag
    existing.decided_by = decided_by

    session.flush()
    logger.info(
        "curation.override.set",
        extra={
            "accession_number": accn,
            "decision": normalized_decision,
            "source_tag": source_tag,
            "event_type": event_type.value if event_type else "UNCHANGED",
        },
    )
    return existing


def bulk_set_overrides(
    session: Session,
    accession_numbers: Iterable[str],
    decision: str,
    *,
    reason: str,
    source_tag: str,
    decided_by: str | None = None,
    decision_source: str = "curated",
) -> dict[str, int]:
    """Registrerer samme afgørelse for flere accessionsnumre.

    Bevidst begrænset til netop de angivne accessionsnumre — kaldere som
    CLI'en har intet loft-argument, der "genindsætter alt". Se
    ``app.cli.cmd_backfill_enqueue``.

    Hvert nummer går gennem :func:`set_override`, så historikreglerne er
    de samme: kun faktiske ændringer giver en historikpost.

    Returns:
        Antal ``created`` (nye overrides), ``updated`` (en eksisterende
        override ændrede sig) og ``unchanged`` (identisk med den
        eksisterende afgørelse — intet skrevet, ingen historikpost).
    """
    normalized_decision = _normalize_decision(decision)
    normalized_reason = _normalize_reason(reason)

    wanted = [str(a).strip() for a in accession_numbers if a is not None and str(a).strip()]
    unique = list(dict.fromkeys(wanted))
    if not unique:
        return {"created": 0, "updated": 0, "unchanged": 0}

    existing = get_overrides(session, unique)

    created = updated = unchanged = 0
    for accn in unique:
        previous = existing.get(accn)
        event_type = _classify_change(
            previous,
            decision=normalized_decision,
            reason=normalized_reason,
            source_tag=source_tag,
            decided_by=decided_by,
        )
        if event_type is CuratedOverrideEventType.CREATED:
            created += 1
        elif event_type is None:
            unchanged += 1
        else:
            updated += 1

        set_override(
            session,
            accn,
            normalized_decision,
            reason=normalized_reason,
            source_tag=source_tag,
            decided_by=decided_by,
            decision_source=decision_source,
        )

    session.flush()
    logger.info(
        "curation.override.bulk_set",
        extra={
            "decision": normalized_decision,
            "source_tag": source_tag,
            # Ikke "created"/"updated": LogRecord reserverer selv "created"
            # (tidsstemplet for logposten), og overskrivning fejler med
            # KeyError ved faktisk logning — kun opdaget ved en rigtig CLI-
            # kørsel, ikke i tests, der ikke fanger logoutput.
            "overrides_created": created,
            "overrides_updated": updated,
            "overrides_unchanged": unchanged,
        },
    )
    return {"created": created, "updated": updated, "unchanged": unchanged}


def clear_override(
    session: Session,
    accession_number: str,
    *,
    reason: str | None = None,
    decided_by: str | None = None,
) -> bool:
    """Fjerner den AKTUELLE kuraterede afgørelse.

    Dokumentet falder tilbage til ren automatisk klassifikation ved
    næste import. Selve afgørelsen forsvinder fra
    `curated_relevance_overrides`, men der skrives en CLEARED-post i
    historikken med den fjernede afgørelse og begrundelse bevaret — så
    det stadig kan aflæses, at der ENGANG var en override, hvad den sagde,
    og hvorfor den blev fjernet.

    Bruges til at rette en fejlregistreret override — ikke en del af den
    almindelige kurateringsarbejdsgang.
    """
    row = get_override(session, accession_number)
    if row is None:
        return False

    _record_event(
        session,
        accession_number=row.accession_number,
        event_type=CuratedOverrideEventType.CLEARED,
        previous=row,
        # Ingen gældende afgørelse bagefter.
        new_decision=None,
        new_reason=(reason or "").strip() or None,
        new_source_tag=None,
        decision_source=row.decision_source,
        decided_by=decided_by,
    )

    session.delete(row)
    session.flush()
    logger.info(
        "curation.override.cleared",
        extra={"accession_number": row.accession_number},
    )
    return True

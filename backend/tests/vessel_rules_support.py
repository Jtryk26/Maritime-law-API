"""Fælles opsætning for Vessel Applicability 2.0-testene.

Regler kan kun udkastes mod høstede dokumenter, så testene opretter først
de kildedokumenter, seedfilen peger på — med seedens tekst indlejret i
omkringliggende lovtekst, så den ordrette søgning faktisk prøves.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.vessel_applicability.batch import DraftBatch, load_payload
from tests.conftest import REPO_ROOT

#: Testmatricen — tre regler, herunder to SOLAS-regler, der ikke må seedes.
SEED_PATH = REPO_ROOT / "backend" / "tests" / "data" / "vessel_rules_matrix.yaml"
SEED = DraftBatch.model_validate(load_payload(str(SEED_PATH)))

#: Standardseeden, som den ligger til brug på serveren.
DEFAULT_SEED_PATH = REPO_ROOT / "data" / "fixtures" / "vessel_applicability_seed.yaml"

PAX_SURVEY = "SOLAS-CAP-I-REG-7-PASSENGER-SURVEY"
CARGO_SURVEY = "SOLAS-CAP-I-REG-8-CARGO-SURVEY-500GT"
SAFETY_ORG = "DK-BEK-2017-863-SEC-4-SAFETY-ORG"

REVIEWER = "jacob"
STATEMENT = "Kontrolleret mod den høstede tekst i testen."


def add_source_document(
    session, source_id: str, content: str, *, version: int = 1, status: str = "Gældende"
):
    """Opret (eller udvid) et høstet dokument med en ny aktuel version."""
    from sqlalchemy import select

    from app.models import Document, DocumentVersion

    document = session.scalar(select(Document).where(Document.source_id == source_id))
    if document is None:
        document = Document(
            source="retsinformation",
            source_id=source_id,
            title=f"Testdokument {source_id}",
            status=status,
            is_maritime=True,
            maritime_score=90,
        )
        session.add(document)
        session.flush()
    row = DocumentVersion(
        document_id=document.id,
        version_number=version,
        content=content,
        content_hash=f"{version:064d}",
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    document.current_version_id = row.id
    session.commit()
    return row


def seed_source_documents(session, batch: DraftBatch = SEED) -> None:
    """Ét dokument pr. source_id, der indeholder alle de citater, der peger på det.

    Linjeskiftene ændres bevidst: Retsinformation ombryder anderledes end et
    udklip, og den ordrette søgning skal tåle det.
    """
    texts: dict[str, list[str]] = {}
    for entry in batch.drafts:
        texts.setdefault(entry.source_document.source_id, []).append(entry.source_text)
    for source_id, quotes in texts.items():
        body = "\n\n".join(" ".join(q.split()) for q in dict.fromkeys(quotes))
        content = f"Indledende bestemmelser.\n\n{body}\n\nIkrafttræden."
        add_source_document(session, source_id, content)


def ingest(session, batch: DraftBatch = SEED) -> dict[str, str]:
    """Indlæs som `vessel-rules ingest` gør. Returnerer rule_id → content_hash."""
    from app.services.vessel_applicability import VesselApplicabilityService

    service = VesselApplicabilityService(session)
    hashes = {}
    for entry in batch.drafts:
        plan = service.save_draft(
            entry.rule,
            source_text=entry.source_text,
            source=entry.source_document.to_ref(),
            drafted_by=batch.generator.actor,
        )
        hashes[plan.rule_id] = plan.content_hash
    session.commit()
    return hashes


def approve(session, hashes: dict[str, str], reviewer: str = REVIEWER) -> None:
    from app.services.vessel_applicability import VesselApplicabilityService

    service = VesselApplicabilityService(session)
    for rule_id, digest in hashes.items():
        service.approve(
            rule_id, reviewer_id=reviewer, signature=STATEMENT, expected_content_hash=digest
        )
    session.commit()


def seed_and_approve(session, batch: DraftBatch = SEED) -> dict[str, str]:
    seed_source_documents(session, batch)
    hashes = ingest(session, batch)
    approve(session, hashes)
    return hashes

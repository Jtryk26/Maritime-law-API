"""Understøttende søgning — aldrig beslutning.

Vektorsøgningen finder den lovtekst, et menneske bør læse ved manuel
gennemgang. Den afgør ikke, om reglen gælder.

Arkitekturen håndhæver det:

* :func:`evaluate_applicability` tager ingen retriever og intet fragment som
  argument. Den *kan* ikke bruge dem, uanset hvad kalderen gør.
* Fragmenter tilknyttes bagefter med :func:`attach_supporting_fragments`, som
  kaster, hvis afgørelse eller konfidens ændrer sig.
* ``SupportingFragment.influenced_verdict`` er altid falsk.

Der beregnes ingen nye indlejringer her. Vektorerne er dem, importen allerede
har lagt i ``document_chunks``.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.text import fold, tokenize
from app.core.vectors import cosine_similarity, unpack_vector
from app.models import DocumentChunk

from .engine import ApplicabilityResult, SupportingFragment
from .profile import VesselProfile

__all__ = [
    "fetch_supporting_fragments",
    "attach_supporting_fragments",
    "build_retrieval_text",
]


def build_retrieval_text(profile: VesselProfile) -> str:
    """Deterministisk søgestreng udledt af profilen.

    Ingen sprogmodel: en fast tabel fra strukturerede værdier til de danske ord,
    lovteksten bruger.
    """
    terms: list[str] = []
    for vessel_type in profile.all_vessel_types:
        terms.extend(_VESSEL_TERMS.get(vessel_type.value, ()))
    for operation in profile.operation_types:
        terms.extend(_OPERATION_TERMS.get(operation.value, ()))
    terms.append("anvendelsesområde")
    seen: list[str] = []
    for term in terms:
        if term not in seen:
            seen.append(term)
    return " ".join(seen)


_VESSEL_TERMS: dict[str, tuple[str, ...]] = {
    "passenger_ship": ("passagerskib", "passagerer"),
    "ro_ro_passenger_ship": ("ro-ro-passagerskib", "passagerskib", "færge"),
    "high_speed_passenger_craft": ("hurtigfærge", "passagerskib"),
    "oil_tanker": ("olietankskib", "tankskib"),
    "chemical_tanker": ("kemikalietankskib", "tankskib"),
    "gas_carrier": ("gastankskib", "tankskib"),
    "general_cargo_ship": ("lastskib", "handelsskib"),
    "container_ship": ("containerskib", "lastskib"),
    "bulk_carrier": ("bulkskib", "lastskib"),
    "ro_ro_cargo_ship": ("ro-ro-lastskib", "lastskib"),
    "fishing_vessel": ("fiskeskib", "fiskefartøj"),
    "offshore_support_vessel": ("offshorefartøj", "forsyningsskib"),
    "rov_support_vessel": ("rov", "offshorefartøj"),
    "dive_support_vessel": ("dykkerskib", "dykkerarbejde"),
    "cable_layer": ("kabelskib",),
    "tug": ("slæbebåd", "bugsering"),
    "dredger": ("uddybningsfartøj",),
    "training_vessel": ("skoleskib",),
    "pleasure_craft": ("fritidsfartøj",),
    "other": ("skib",),
}

_OPERATION_TERMS: dict[str, tuple[str, ...]] = {
    "international_voyage": ("international fart",),
    "domestic_voyage": ("indenrigsfart", "national fart"),
    "near_coastal": ("kystnær fart",),
    "harbour_service": ("havnefart",),
    "inland_waterway": ("indre farvande",),
    "fishing_operation": ("fiskeri",),
    "offshore_construction": ("offshore",),
    "rov_operation": ("rov",),
    "dive_operation": ("dykkerarbejde",),
    "standby_rescue": ("beredskabsfartøj",),
    "wind_farm_service": ("havvindmøllepark",),
    "towage": ("bugsering",),
    "laid_up": ("oplagt",),
}


def fetch_supporting_fragments(
    session: Session,
    *,
    document_ids: list[int],
    query_vector: np.ndarray | None = None,
    query_text: str | None = None,
    limit: int = 5,
) -> dict[int, list[SupportingFragment]]:
    """Finder skopfragmenter pr. dokument. Rører ikke afgørelsen.

    Har dokumenterne vektorer, og er der givet en forespørgselsvektor, bruges
    cosinuslighed. Ellers falder den tilbage til en foldet ordoverlapning, så
    funktionen også virker på en database uden indeks.
    """
    if not document_ids:
        return {}

    rows = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id.in_(document_ids))
            .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
        )
    )
    if not rows:
        return {}

    scored: list[tuple[float, DocumentChunk, str]] = []
    if query_vector is not None:
        for chunk in rows:
            vector = unpack_vector(chunk.embedding)
            if vector is None or vector.shape[0] != query_vector.shape[0]:
                continue
            score = float(cosine_similarity(query_vector, vector.reshape(1, -1))[0])
            scored.append((score, chunk, "vector"))

    if not scored and query_text:
        terms = {token for token in tokenize(query_text) if len(token) > 2}
        if terms:
            for chunk in rows:
                haystack = fold(f"{chunk.heading or ''} {chunk.content}")
                hits = sum(1 for term in terms if term in haystack)
                if hits:
                    scored.append((round(hits / len(terms), 4), chunk, "lexical"))

    by_document: dict[int, list[SupportingFragment]] = {}
    for score, chunk, method in sorted(scored, key=lambda item: (-item[0], item[1].id)):
        bucket = by_document.setdefault(chunk.document_id, [])
        if len(bucket) >= limit:
            continue
        bucket.append(
            SupportingFragment(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                ref=chunk.full_citation or chunk.paragraph_id or chunk.heading or "",
                text=chunk.content,
                score=score,
                method=method,
                document_version_id=chunk.version_id,
                influenced_verdict=False,
            )
        )
    return by_document


def attach_supporting_fragments(
    result: ApplicabilityResult, fragments: list[SupportingFragment]
) -> ApplicabilityResult:
    """Knytter fragmenter til et resultat og bevogter invarianten."""
    verdict_before = result.verdict
    confidence_before = result.confidence

    result.supporting_fragments = [
        SupportingFragment(
            chunk_id=fragment.chunk_id,
            document_id=fragment.document_id,
            ref=fragment.ref,
            text=fragment.text,
            score=fragment.score,
            method=fragment.method,
            document_version_id=fragment.document_version_id,
            influenced_verdict=False,
        )
        for fragment in fragments
        if fragment.document_id == result.document_id
    ]

    if result.verdict is not verdict_before or result.confidence != confidence_before:
        raise RuntimeError(
            "Invariant brudt: understøttende fragmenter må aldrig ændre afgørelse "
            "eller konfidens."
        )
    return result

"""Ruter for geografiske områder og dokumenters geografi (eksperimentel).

Registreres kun med GEOGRAPHY_ENABLED=true (se `app/api/routes/__init__.py`).
`router` er offentlig og læsende. `admin_router` får administratorkravet på
routerniveau, ligesom `imports`.

Gennemgået og automatisk holdes adskilt
=======================================
* ``/geographic-areas/{slug}/documents`` og ``/documents/{id}/geography``
  returnerer KUN tilknytninger, et menneske har godkendt for dokumentets
  aktuelle version, titel, status og områdets aktuelle definition
  (`services.geography.is_published`).
* Automatiske fund — også dem, hvis tidligere godkendelse er forældet —
  findes kun under de separate ``…/detections``-endepunkter, hvor hvert svar
  og hvert element bærer ``result_kind = "automatic_detection"`` og
  etiketten "Detected … — not reviewed".
* Gennemgange af tidligere tekst står under ``review_history`` og som
  ``previous_review`` (kun kontekst) — aldrig som gældende.
* Afviste fund vises aldrig. Hjemmel og datoer for ikke-efterprøvede
  områder står kun under ``provisional``. Dokumenter fra rettighedsstyrede
  kilder udelades som i resten af API'et.

Der findes bevidst ingen rute, der afgør, om en position eller rute ligger i
et område. `bbox` er et visningsudsnit.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import distinct, func, or_, select

from app.api.deps import DbSession, Pagination
from app.models import Document
from app.models.geography import (
    APPROVED_REVIEW_STATUSES,
    Applicability,
    DocumentGeographicApplicability as Match,
    EvidenceStatus,
    GeographicArea,
    GeographicDetectionRun,
    GeometryKind,
    GeoReviewStatus,
)
from app.schemas import Page
from app.schemas.geography import (
    DETECTION_NOTICE,
    GEOMETRY_LABELS,
    AreaDocumentOut,
    AreaDocumentPage,
    DocumentAreaOut,
    DocumentDetectionsOut,
    DocumentGeographyOut,
    GeoFeatureCollectionOut,
    GeographicAreaDetailOut,
    GeographicAreaSummaryOut,
    GeographicMatchOut,
    GeographyAnalysisOut,
    PreviousReviewOut,
    ProvisionalMetadataOut,
)
from app.services.geography import PUBLICATION_LABELS, is_published, publication_status

router = APIRouter(tags=["geografi"])
admin_router = APIRouter(tags=["geografi"])

REVIEWED = "reviewed_applicability"
DETECTED = "automatic_detection"
REVIEWED_NOTICE = (
    "Reviewed geographic applicability only: approved by a person for the current "
    "document text. Automatic detections are not included."
)

_CURRENT = (
    (Match.review_status != GeoReviewStatus.REJECTED.value)
    & (Match.evidence_status == EvidenceStatus.CURRENT.value)
)
#: Ved samme confidence: den mest handlingsrelevante klassifikation først.
_PRECEDENCE = {
    Applicability.EXEMPTS.value: 5,
    Applicability.APPLIES.value: 4,
    Applicability.DEFINES.value: 3,
    Applicability.HISTORICAL.value: 2,
    Applicability.MENTIONS.value: 1,
}


def _public_documents():
    from app.services.rights import legacy_documents

    return select(Document.id).where(legacy_documents())


def _approved_candidates():
    """SQL-forfilter for godkendte fund. Konteksten afgøres i Python (`is_published`)."""
    return (
        _CURRENT,
        Match.review_status.in_(APPROVED_REVIEW_STATUSES),
        Match.document_version_id.in_(select(Document.current_version_id)),
        Match.document_id.in_(_public_documents()),
    )


def _detection_conditions():
    """Aktuelle fund, der ikke er (gyldigt) godkendt: automatiske og forældede."""
    current_versions = select(Document.current_version_id).where(Document.current_version_id.is_not(None))
    return (
        _CURRENT,
        Match.document_id.in_(_public_documents()),
        or_(
            Match.review_status.not_in(APPROVED_REVIEW_STATUSES),
            Match.document_version_id.is_(None),
            Match.document_version_id.not_in(current_versions),
        ),
    )


def _verified(area: GeographicArea) -> bool:
    return area.legal_status == "verified"


def _display_extent(area: GeographicArea) -> list[float] | None:
    """Kun for flader og linjer. Et kortmærke har ingen udstrækning."""
    if area.min_lon is None or area.geometry_kind == GeometryKind.INDICATIVE_POINT.value:
        return None
    return [area.min_lon, area.min_lat, area.max_lon, area.max_lat]


def _published_rows(session, area_ids: list[int], *extra) -> list[Match]:
    """Godkendte fund, der stadig gælder for dokumentets aktuelle tekst og kontekst."""
    if not area_ids:
        return []
    rows = session.scalars(select(Match).where(Match.area_id.in_(area_ids), *_approved_candidates(), *extra)).all()
    if not rows:
        return []
    documents = {d.id: d for d in session.scalars(select(Document).where(Document.id.in_({r.document_id for r in rows})))}
    areas = {a.id: a for a in session.scalars(select(GeographicArea).where(GeographicArea.id.in_({r.area_id for r in rows})))}
    return [r for r in rows if is_published(r, documents[r.document_id], areas.get(r.area_id))]


def _counts(session, area_ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
    """(godkendte, automatiske) dokumenter pr. område."""
    reviewed: dict[int, set[int]] = defaultdict(set)
    for row in _published_rows(session, area_ids):
        reviewed[row.area_id].add(row.document_id)
    detected = dict(
        session.execute(
            select(Match.area_id, func.count(distinct(Match.document_id)))
            .where(Match.area_id.in_(area_ids), *_detection_conditions())
            .group_by(Match.area_id)
        ).all()
    ) if area_ids else {}
    return {k: len(v) for k, v in reviewed.items()}, detected


def _summary(area: GeographicArea, counts=None) -> GeographicAreaSummaryOut:
    reviewed, detected = counts or ({}, {})
    verified = _verified(area)
    return GeographicAreaSummaryOut(
        slug=area.slug,
        name=area.name,
        name_da=area.name_da,
        area_type=area.area_type,
        related_area_slug=area.parent_slug,
        legal_status=area.legal_status,
        metadata_verified=verified,
        geometry_kind=area.geometry_kind,
        geometry_label=GEOMETRY_LABELS.get(area.geometry_kind, area.geometry_kind),
        display_extent=_display_extent(area),
        effective_from=area.effective_from if verified else None,
        effective_to=area.effective_to if verified else None,
        reviewed_document_count=reviewed.get(area.id, 0),
        detected_document_count=detected.get(area.id, 0),
    )


def _load_area(session, slug: str) -> GeographicArea:
    area = session.scalars(select(GeographicArea).where(GeographicArea.slug == slug)).first()
    if area is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Område '{slug}' findes ikke.")
    return area


def _load_document(session, document_id: int) -> Document:
    document = session.scalars(
        select(Document).where(Document.id == document_id, Document.id.in_(_public_documents()))
    ).first()
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Dokument med id {document_id} findes ikke.")
    return document


def _parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    try:
        parts = [float(p) for p in value.split(",")]
    except ValueError:
        parts = []
    if len(parts) != 4 or parts[0] > parts[2] or parts[1] > parts[3]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="bbox skal være 'min_lon,min_lat,max_lon,max_lat'.",
        )
    return parts[0], parts[1], parts[2], parts[3]


def _area_query(area_type: list[str] | None, bbox: str | None):
    query = select(GeographicArea)
    if area_type:
        query = query.where(GeographicArea.area_type.in_(area_type))
    box = _parse_bbox(bbox)
    if box:
        min_lon, min_lat, max_lon, max_lat = box
        query = query.where(
            GeographicArea.min_lon <= max_lon,
            GeographicArea.max_lon >= min_lon,
            GeographicArea.min_lat <= max_lat,
            GeographicArea.max_lat >= min_lat,
        )
    return query


def _previous_reviews(session, rows: list[Match]) -> dict[int, PreviousReviewOut]:
    """Den ældre afgørelse, hvert fund afløser — som kontekst."""
    ids = {r.supersedes_id for r in rows if r.supersedes_id}
    if not ids:
        return {}
    olds = {o.id: o for o in session.scalars(select(Match).where(Match.id.in_(ids)))}
    out = {}
    for row in rows:
        old = olds.get(row.supersedes_id)
        if old is None or old.review_status == GeoReviewStatus.UNREVIEWED.value:
            continue
        out[row.id] = PreviousReviewOut(
            id=old.id,
            review_status=old.review_status,
            applicability=old.applicability,
            reviewed_by=old.reviewed_by,
            reviewed_at=old.reviewed_at,
            document_version_id=old.document_version_id,
            excerpt_unchanged=bool(old.evidence_hash) and old.evidence_hash == row.evidence_hash,
        )
    return out


def _match_out(row: Match, document: Document, area, previous=None) -> GeographicMatchOut:
    state = publication_status(row, document, area)
    return GeographicMatchOut(
        id=row.id,
        document_version_id=row.document_version_id,
        source_field=row.source_field,
        match_text=row.match_text,
        char_start=row.char_start,
        char_end=row.char_end,
        paragraph_anchor=row.paragraph_anchor,
        source_excerpt=row.context,
        applicability=row.applicability,
        confidence=row.confidence,
        cue=row.cue,
        detection_method=row.detection_method,
        detector_version=row.detector_version,
        review_status=row.review_status,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        evidence_status=row.evidence_status,
        supersedes_id=row.supersedes_id,
        publication_status=state,
        publication_label=PUBLICATION_LABELS[state],
        previous_review=(previous or {}).get(row.id),
    )


def _best(rows: list[Match]) -> Match:
    return max(rows, key=lambda r: (r.confidence, _PRECEDENCE.get(r.applicability, 0), -r.id))


def _area_item(kind, document: Document, rows: list[Match], areas, previous) -> AreaDocumentOut:
    best = _best(rows)
    state = publication_status(best, document, areas.get(best.area_id))
    anchor = best.paragraph_anchor
    return AreaDocumentOut(
        result_kind=kind,
        document_id=document.id,
        title=document.display_title or document.title,
        document_type=document.document_type,
        document_number=document.document_number,
        status=document.status,
        published_date=document.published_date,
        effective_date=document.effective_date,
        law_class=document.law_class,
        vessel_relevance=list(document.niche_groups or []),
        source_url=document.source_url,
        document_path=f"#/dokument/{document.id}" + (f"?p={anchor}" if anchor else ""),
        area_slug=areas[best.area_id].slug,
        applicability=best.applicability,
        confidence=best.confidence,
        review_status=best.review_status,
        publication_status=state,
        publication_label=PUBLICATION_LABELS[state],
        match_count=len(rows),
        match_text=best.match_text,
        source_field=best.source_field,
        detection_method=best.detection_method,
        document_version_id=best.document_version_id,
        paragraph_anchor=anchor,
        source_excerpt=best.context,
        previous_review=previous.get(best.id),
    )


def _page(kind, notice, items, total, page, page_size) -> AreaDocumentPage:
    return AreaDocumentPage(
        result_kind=kind,
        notice=notice,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


def _areas_for(session, slug: str, include_related: bool) -> dict[int, GeographicArea]:
    area = _load_area(session, slug)
    areas = {area.id: area}
    if include_related:
        for child in session.scalars(select(GeographicArea).where(GeographicArea.parent_slug == area.slug)):
            areas[child.id] = child
    return areas


IncludeRelated = Annotated[
    bool,
    Query(
        description=(
            "Medtag geografisk tilknyttede områder (fx ECA og PSSA under Østersøen). "
            "Hvert resultat angiver sit eget område i area_slug; de er forskellige juridiske områder."
        )
    ),
]
ApplicabilityFilter = Annotated[
    list[Applicability] | None,
    Query(description="Klassifikation. Standard: alle undtagen MENTIONS."),
]


def _wanted(applicability) -> list[str]:
    if applicability:
        return [a.value for a in applicability]
    return [a.value for a in Applicability if a != Applicability.MENTIONS]


# ---------------------------------------------------------------------------
# Områder
# ---------------------------------------------------------------------------


@router.get(
    "/geographic-areas",
    response_model=Page[GeographicAreaSummaryOut],
    summary="Geografiske områder (eksperimentel)",
)
def list_areas(
    session: DbSession,
    pagination: Pagination,
    area_type: Annotated[list[str] | None, Query(description="Områdetype. Kan gentages.")] = None,
    bbox: Annotated[
        str | None,
        Query(
            description=(
                "Visningsudsnit 'min_lon,min_lat,max_lon,max_lat': områder, hvis kortgeometri "
                "skal tegnes i udsnittet. Afgør IKKE, om en position ligger i et område."
            )
        ),
    ] = None,
) -> Page[GeographicAreaSummaryOut]:
    page, page_size = pagination
    query = _area_query(area_type, bbox)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    areas = session.scalars(
        query.order_by(GeographicArea.area_type, GeographicArea.slug)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    counts = _counts(session, [a.id for a in areas])
    return Page(
        items=[_summary(a, counts) for a in areas],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


@router.get(
    "/geographic-areas/map",
    response_model=GeoFeatureCollectionOut,
    summary="Kortdata (GeoJSON) for MapLibre (eksperimentel)",
)
def map_features(
    session: DbSession,
    area_type: Annotated[list[str] | None, Query(description="Områdetype. Kan gentages.")] = None,
    bbox: Annotated[str | None, Query(description="Visningsudsnit 'min_lon,min_lat,max_lon,max_lat'.")] = None,
) -> GeoFeatureCollectionOut:
    areas = session.scalars(_area_query(area_type, None).order_by(GeographicArea.slug)).all()
    counts = _counts(session, [a.id for a in areas])
    box = _parse_bbox(bbox)
    features, unmapped = [], []
    for area in areas:
        summary = _summary(area, counts)
        if area.geometry is None:
            unmapped.append(summary)
            continue
        if box and not (
            area.min_lon <= box[2] and area.max_lon >= box[0]
            and area.min_lat <= box[3] and area.max_lat >= box[1]
        ):
            continue
        features.append(
            {
                "type": "Feature",
                "id": area.id,
                "geometry": area.geometry,
                "properties": {
                    "slug": area.slug,
                    "name": area.name_da or area.name,
                    "area_type": area.area_type,
                    "geometry_kind": area.geometry_kind,
                    "geometry_label": summary.geometry_label,
                    "legal_status": area.legal_status,
                    "reviewed_document_count": summary.reviewed_document_count,
                    "detected_document_count": summary.detected_document_count,
                },
            }
        )
    return GeoFeatureCollectionOut(features=features, unmapped=unmapped)


@router.get(
    "/geographic-areas/{slug}",
    response_model=GeographicAreaDetailOut,
    summary="Ét geografisk område",
)
def get_area(session: DbSession, slug: Annotated[str, Path(max_length=128)]) -> GeographicAreaDetailOut:
    area = _load_area(session, slug)
    related = session.scalars(
        select(GeographicArea.slug).where(GeographicArea.parent_slug == area.slug).order_by(GeographicArea.slug)
    ).all()
    reviewed_by_type: dict[str, set[int]] = defaultdict(set)
    for row in _published_rows(session, [area.id]):
        reviewed_by_type[row.applicability].add(row.document_id)
    detected_by_type = dict(
        session.execute(
            select(Match.applicability, func.count(distinct(Match.document_id)))
            .where(Match.area_id == area.id, *_detection_conditions())
            .group_by(Match.applicability)
        ).all()
    )
    verified = _verified(area)
    has_provisional = not verified and (area.legal_basis or area.effective_from or area.effective_to)
    metadata = dict(area.metadata_json or {})
    summary = _summary(area, _counts(session, [area.id]))
    return GeographicAreaDetailOut(
        **summary.model_dump(),
        legal_basis=area.legal_basis if verified else None,
        provisional=(
            ProvisionalMetadataOut(
                legal_basis=area.legal_basis,
                effective_from=area.effective_from,
                effective_to=area.effective_to,
            )
            if has_provisional
            else None
        ),
        reference_url=area.source_url,
        source_reference=area.source_reference,
        designation=dict(metadata.get("designation") or {}),
        geometry=area.geometry,
        geometry_source=area.geometry_source,
        aliases=list(area.aliases or []),
        related_areas=list(related),
        reviewed_counts_by_applicability={k: len(v) for k, v in reviewed_by_type.items()},
        detected_counts_by_applicability=detected_by_type,
    )


@router.get(
    "/geographic-areas/{slug}/documents",
    response_model=AreaDocumentPage,
    summary="Gennemgået geografisk anvendelighed for netop dette område",
)
def area_documents(
    session: DbSession,
    pagination: Pagination,
    slug: Annotated[str, Path(max_length=128)],
    applicability: ApplicabilityFilter = None,
    include_related: IncludeRelated = False,
) -> AreaDocumentPage:
    """KUN tilknytninger, et menneske har godkendt for dokumentets aktuelle tekst.

    Automatiske fund: ``GET /geographic-areas/{slug}/detections``.
    """
    page, page_size = pagination
    areas = _areas_for(session, slug, include_related)
    rows = _published_rows(session, list(areas), Match.applicability.in_(_wanted(applicability)))
    by_document: dict[int, list[Match]] = defaultdict(list)
    for row in rows:
        by_document[row.document_id].append(row)
    ordered = sorted(by_document, key=lambda d: (-_best(by_document[d]).confidence, d))
    page_ids = ordered[(page - 1) * page_size: page * page_size]
    documents = {d.id: d for d in session.scalars(select(Document).where(Document.id.in_(page_ids)))}
    items = [_area_item(REVIEWED, documents[d], by_document[d], areas, {}) for d in page_ids]
    return _page(REVIEWED, REVIEWED_NOTICE, items, len(ordered), page, page_size)


@router.get(
    "/geographic-areas/{slug}/detections",
    response_model=AreaDocumentPage,
    summary="Automatiske geografiske fund — ikke gennemgået",
)
def area_detections(
    session: DbSession,
    pagination: Pagination,
    slug: Annotated[str, Path(max_length=128)],
    applicability: ApplicabilityFilter = None,
    include_related: IncludeRelated = False,
    min_confidence: Annotated[float, Query(ge=0, le=1)] = 0.0,
) -> AreaDocumentPage:
    """Regelbaserede fund, ingen har godkendt for den aktuelle tekst. Ikke anvendelighed."""
    page, page_size = pagination
    areas = _areas_for(session, slug, include_related)
    conditions = [
        Match.area_id.in_(list(areas)),
        Match.applicability.in_(_wanted(applicability)),
        Match.confidence >= min_confidence,
        *_detection_conditions(),
    ]
    grouped = (
        select(Match.document_id, func.max(Match.confidence).label("best"))
        .where(*conditions)
        .group_by(Match.document_id)
    )
    total = session.scalar(select(func.count()).select_from(grouped.subquery())) or 0
    page_ids = [
        row.document_id
        for row in session.execute(
            grouped.order_by(func.max(Match.confidence).desc(), Match.document_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ]
    by_document: dict[int, list[Match]] = defaultdict(list)
    for row in session.scalars(select(Match).where(*conditions, Match.document_id.in_(page_ids))):
        by_document[row.document_id].append(row)
    documents = {d.id: d for d in session.scalars(select(Document).where(Document.id.in_(page_ids)))}
    previous = _previous_reviews(session, [r for rows in by_document.values() for r in rows])
    items = [_area_item(DETECTED, documents[d], by_document[d], areas, previous) for d in page_ids]
    return _page(DETECTED, DETECTION_NOTICE, items, total, page, page_size)


# ---------------------------------------------------------------------------
# Dokumentets geografi
# ---------------------------------------------------------------------------


def _document_areas(session, document: Document, rows: list[Match], kind) -> list[DocumentAreaOut]:
    by_area: dict[int, list[Match]] = defaultdict(list)
    for row in rows:
        by_area[row.area_id].append(row)
    areas = {a.id: a for a in session.scalars(select(GeographicArea).where(GeographicArea.id.in_(list(by_area))))}
    counts = _counts(session, list(areas))
    previous = _previous_reviews(session, rows) if kind == DETECTED else {}
    out = []
    for area_id, area_rows in by_area.items():
        best = _best(area_rows)
        state = publication_status(best, document, areas[area_id])
        out.append(
            DocumentAreaOut(
                result_kind=kind,
                area=_summary(areas[area_id], counts),
                applicability=best.applicability,
                confidence=best.confidence,
                review_status=best.review_status,
                publication_status=state,
                publication_label=PUBLICATION_LABELS[state],
                matches=[_match_out(r, document, areas[area_id], previous) for r in area_rows],
            )
        )
    out.sort(key=lambda a: (-_PRECEDENCE.get(a.applicability, 0), -a.confidence, a.area.slug))
    return out


def _current_rows(session, document: Document) -> tuple[list[Match], list[Match]]:
    """(godkendte og gældende, øvrige aktuelle) for et dokument."""
    rows = session.scalars(
        select(Match).where(Match.document_id == document.id, _CURRENT).order_by(Match.id)
    ).all()
    areas = {a.id: a for a in session.scalars(select(GeographicArea).where(GeographicArea.id.in_({r.area_id for r in rows})))}
    published = [r for r in rows if is_published(r, document, areas.get(r.area_id))]
    ids = {r.id for r in published}
    return published, [r for r in rows if r.id not in ids]


@router.get(
    "/documents/{document_id}/geography",
    response_model=DocumentGeographyOut,
    summary="Gennemgået geografisk anvendelighed for et dokument",
)
def document_geography(
    session: DbSession, document_id: Annotated[int, Path(ge=1)]
) -> DocumentGeographyOut:
    """KUN godkendte, gældende tilknytninger og historik. Automatiske fund: ``…/geography/detections``."""
    document = _load_document(session, document_id)
    published, detections = _current_rows(session, document)
    history = session.scalars(
        select(Match).where(
            Match.document_id == document.id,
            Match.evidence_status == EvidenceStatus.SUPERSEDED.value,
            Match.review_status.in_(APPROVED_REVIEW_STATUSES),
        ).order_by(Match.id)
    ).all()
    history_areas = {a.id: a for a in session.scalars(select(GeographicArea).where(GeographicArea.id.in_({r.area_id for r in history})))}
    run = session.scalars(
        select(GeographicDetectionRun)
        .where(GeographicDetectionRun.document_id == document.id, GeographicDetectionRun.status == "COMPLETED")
        .order_by(GeographicDetectionRun.id.desc())
        .limit(1)
    ).first()
    return DocumentGeographyOut(
        document_id=document.id,
        title=document.display_title or document.title,
        current_version_id=document.current_version_id,
        detector_version=run.detector_version if run else None,
        analysed_at=run.finished_at if run else None,
        areas=_document_areas(session, document, published, REVIEWED),
        review_history=[_match_out(r, document, history_areas.get(r.area_id)) for r in history],
        detected_area_count=len(Counter(r.area_id for r in detections)),
        detections_path=f"/documents/{document.id}/geography/detections",
    )


@router.get(
    "/documents/{document_id}/geography/detections",
    response_model=DocumentDetectionsOut,
    summary="Automatiske geografiske fund i et dokument — ikke gennemgået",
)
def document_geography_detections(
    session: DbSession, document_id: Annotated[int, Path(ge=1)]
) -> DocumentDetectionsOut:
    document = _load_document(session, document_id)
    _, detections = _current_rows(session, document)
    return DocumentDetectionsOut(
        document_id=document.id,
        title=document.display_title or document.title,
        current_version_id=document.current_version_id,
        areas=_document_areas(session, document, detections, DETECTED),
    )


# ---------------------------------------------------------------------------
# Drift (administratortoken)
# ---------------------------------------------------------------------------


@admin_router.post(
    "/admin/geography/reanalyse/{document_id}",
    response_model=GeographyAnalysisOut,
    summary="Analysér et dokuments geografi igen",
)
def reanalyse_document(
    session: DbSession,
    document_id: Annotated[int, Path(ge=1)],
    force: Annotated[bool, Query(description="Også selvom tekst og detektor er uændrede.")] = True,
) -> GeographyAnalysisOut:
    from app.services.geography import GeographyService

    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Dokument med id {document_id} findes ikke.")
    result = GeographyService(session).analyse(document, trigger="admin", force=force)
    session.commit()
    return GeographyAnalysisOut(
        document_id=result.document_id,
        status=result.status,
        run_id=result.run_id,
        matches=result.matches,
        queued=result.queued,
        by_area=result.by_area,
    )

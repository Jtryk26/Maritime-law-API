"""Pydantic-skemaer for geografiske områder og dokumenters fund i dem.

Værn mod at præsentere vurderinger som fakta:

* **Gennemgået og automatisk adskilt.** Standardsvarene
  (``…/documents``, ``/documents/{id}/geography``) indeholder KUN
  tilknytninger, et menneske har godkendt for dokumentets aktuelle tekst og
  kontekst (`result_kind = "reviewed_applicability"`). Automatiske fund
  findes kun under de separate ``…/detections``-endepunkter
  (`result_kind = "automatic_detection"`) med etiketten "Detected … — not
  reviewed". Confidence er aldrig godkendelse.
* **Tidligere gennemgang er kontekst.** `previous_review` viser en ældre,
  forældet afgørelse og om uddraget er uændret — den gælder ikke.
* **Foreløbige metadata.** Hjemmel og datoer for et område, der ikke er
  efterprøvet, står kun under `provisional`.
* **Geometri.** `geometry_label` siger, hvad kortet viser. Et kortmærke er
  ikke en grænse, og `display_extent` er et visningsudsnit.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

DISCLAIMER = (
    "Experimental educational overview — not for navigation and not legal advice. "
    "Only results with result_kind 'reviewed_applicability' have been approved by a "
    "person for the current document text; check the official text regardless. Map "
    "markers are approximate and never show whether a position lies inside a legal area."
)

DETECTION_NOTICE = (
    "Automatic detections — not reviewed. These are rule-based readings of the text, "
    "not approved geographic applicability."
)

GEOMETRY_LABELS = {
    "none": "No map geometry",
    "indicative_point": "Approximate area marker",
    "approximate": "Approximate outline — not a legal boundary",
    "official": "Boundary copied from an official source — the official source prevails",
}

ResultKind = Literal["reviewed_applicability", "automatic_detection"]


class ProvisionalMetadataOut(BaseModel):
    """Indtastet, men ikke efterprøvet mod den officielle kilde. Ikke fakta."""

    status_note: str = (
        "Provisional, unverified metadata — not checked against the official legal text."
    )
    legal_basis: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class GeographicAreaSummaryOut(BaseModel):
    slug: str
    name: str
    name_da: str | None = None
    area_type: str
    #: Geografisk tilknytning til navigation (fx ECA -> havet). Ikke juridisk identitet.
    related_area_slug: str | None = None
    legal_status: str
    #: True kun for `legal_status = "verified"`.
    metadata_verified: bool = False
    geometry_kind: str
    geometry_label: str
    #: [min_lon, min_lat, max_lon, max_lat] for kortets visningsudsnit. Kun for
    #: flader/linjer; None for et kortmærke. Aldrig en indeholdelsestest.
    display_extent: list[float] | None = None
    #: Kun for efterprøvede områder; ellers under `provisional` i detaljen.
    effective_from: date | None = None
    effective_to: date | None = None
    #: Dokumenter med en gældende, menneskeligt godkendt tilknytning.
    reviewed_document_count: int = 0
    #: Dokumenter med automatiske, ikke-gennemgåede fund. Ikke anvendelighed.
    detected_document_count: int = 0


class GeographicAreaDetailOut(GeographicAreaSummaryOut):
    #: Kun for efterprøvede områder.
    legal_basis: str | None = None
    provisional: ProvisionalMetadataOut | None = None
    reference_url: str | None = None
    source_reference: str | None = None
    designation: dict[str, Any] = {}
    geometry: dict[str, Any] | None = None
    geometry_source: str | None = None
    aliases: list[str] = []
    related_areas: list[str] = []
    reviewed_counts_by_applicability: dict[str, int] = {}
    detected_counts_by_applicability: dict[str, int] = {}
    disclaimer: str = DISCLAIMER


class PreviousReviewOut(BaseModel):
    """En ældre afgørelse for et tidligere tekstgrundlag. Kun kontekst — gælder ikke."""

    id: int
    review_status: str
    applicability: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    document_version_id: int | None = None
    #: Er det fundne uddrag ordret det samme? Selv da gælder afgørelsen ikke:
    #: konteksten (tekst andetsteds, titel, status, version, område) er ændret.
    excerpt_unchanged: bool
    note: str = "Context only — this earlier decision is not applied to the current text."


class GeographicMatchOut(BaseModel):
    id: int
    document_version_id: int | None = None
    source_field: str
    match_text: str
    char_start: int | None = None
    char_end: int | None = None
    paragraph_anchor: str | None = None
    #: Ordret uddrag af kildeteksten omkring fundet.
    source_excerpt: str | None = None
    applicability: str
    confidence: float
    cue: str | None = None
    detection_method: str
    detector_version: str
    review_status: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    evidence_status: str
    supersedes_id: int | None = None
    publication_status: str
    publication_label: str
    previous_review: PreviousReviewOut | None = None


class AreaDocumentOut(BaseModel):
    """Ét dokument i et område — nok til en popup på kortet."""

    result_kind: ResultKind
    document_id: int
    title: str
    document_type: str | None = None
    document_number: str | None = None
    status: str | None = None
    published_date: date | None = None
    effective_date: date | None = None
    law_class: str | None = None
    #: Nichegrupper fra titlen ("fiskeskibe", "fritidsfartoejer") — et fingerpeg
    #: om fartøjstype, ikke en anvendelighedsvurdering.
    vessel_relevance: list[str] = []
    source_url: str | None = None
    #: Frontend-sti til dokumentet, med paragrafanker når det kendes.
    document_path: str
    area_slug: str
    applicability: str
    confidence: float
    review_status: str
    publication_status: str
    publication_label: str
    match_count: int
    match_text: str
    source_field: str
    detection_method: str
    document_version_id: int | None = None
    paragraph_anchor: str | None = None
    source_excerpt: str | None = None
    previous_review: PreviousReviewOut | None = None


class AreaDocumentPage(BaseModel):
    """Sideinddelt svar med en tydelig angivelse af, hvad siden indeholder."""

    result_kind: ResultKind
    notice: str
    items: list[AreaDocumentOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class DocumentAreaOut(BaseModel):
    result_kind: ResultKind
    area: GeographicAreaSummaryOut
    applicability: str
    confidence: float
    review_status: str
    publication_status: str
    publication_label: str
    matches: list[GeographicMatchOut]


class DocumentGeographyOut(BaseModel):
    """Gennemgåede tilknytninger og historik. Automatiske fund: se `detections_path`."""

    result_kind: ResultKind = "reviewed_applicability"
    document_id: int
    title: str
    current_version_id: int | None = None
    detector_version: str | None = None
    analysed_at: datetime | None = None
    areas: list[DocumentAreaOut]
    #: Gennemgange af tidligere tekst. Bevaret som historik, ikke gældende.
    review_history: list[GeographicMatchOut] = []
    #: Antal områder med automatiske, ikke-gennemgåede fund (ikke indeholdt her).
    detected_area_count: int = 0
    detections_path: str
    disclaimer: str = DISCLAIMER


class DocumentDetectionsOut(BaseModel):
    result_kind: ResultKind = "automatic_detection"
    notice: str = DETECTION_NOTICE
    document_id: int
    title: str
    current_version_id: int | None = None
    areas: list[DocumentAreaOut]
    disclaimer: str = DISCLAIMER


class GeoFeatureCollectionOut(BaseModel):
    """GeoJSON FeatureCollection til MapLibre. `properties` er kompakte."""

    type: str = "FeatureCollection"
    features: list[dict[str, Any]]
    #: Områder uden geometri (fx "Globalt"). Vises som liste, ikke på kortet.
    unmapped: list[GeographicAreaSummaryOut] = []
    disclaimer: str = DISCLAIMER


class GeographyAnalysisOut(BaseModel):
    document_id: int
    status: str
    run_id: int | None = None
    matches: int
    queued: int
    by_area: dict[str, str]

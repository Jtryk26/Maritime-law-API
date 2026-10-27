"""Geografisk anvendelighed: gazetteer, klassifikation og lagring.

Se docs/GEOGRAPHIC_MAP_FEATURE.md. Intet her er en juridisk afgørelse.
"""

from .classify import Classification, classify_match
from .gazetteer import Gazetteer, GazetteerEntry, GazetteerMatch, find_unknown_places, get_gazetteer
from .service import (
    DETECTOR_VERSION,
    PUBLICATION_LABELS,
    RIGHTS_ACTIONS,
    AnalysisResult,
    GeographicAreasMissing,
    GeographyService,
    StaleEvidence,
    effective_geometry_kind,
    geometry_bbox,
    is_published,
    official_geometry_problems,
    publication_status,
    review_context_hash,
    set_geometry,
)

__all__ = [
    "DETECTOR_VERSION",
    "PUBLICATION_LABELS",
    "RIGHTS_ACTIONS",
    "GeographicAreasMissing",
    "StaleEvidence",
    "effective_geometry_kind",
    "official_geometry_problems",
    "publication_status",
    "is_published",
    "review_context_hash",
    "AnalysisResult",
    "Classification",
    "Gazetteer",
    "GazetteerEntry",
    "GazetteerMatch",
    "GeographyService",
    "classify_match",
    "find_unknown_places",
    "geometry_bbox",
    "get_gazetteer",
    "set_geometry",
]

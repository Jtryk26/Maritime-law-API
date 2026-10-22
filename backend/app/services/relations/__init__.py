"""Dokumentrelationer: historiske forgængere, ophævelser og hjemmel.

Se :mod:`.extract` for hvad der udtrækkes, og :mod:`.repository` for
lagring, opslag og læsning.
"""

from .extract import ExtractedRelation, RelationType, extract_relations, law_title_key
from .repository import DocumentRelations, RelatedDocument, RelationRepository, document_keys

__all__ = [
    "DocumentRelations",
    "ExtractedRelation",
    "RelatedDocument",
    "RelationRepository",
    "RelationType",
    "document_keys",
    "extract_relations",
    "law_title_key",
]

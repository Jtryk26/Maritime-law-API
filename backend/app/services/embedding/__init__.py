"""Vektorisering af lovtekst og søgninger.

Offentlig flade::

    build_embedding_provider()   # vælger udbyder ud fra konfiguration
    get_embedding_provider()     # delt, cachet instans
    EmbeddingIndexer             # bygger og vedligeholder chunk-indekset
    chunk_document()             # deler lovtekst i stykker

Resten af systemet importerer herfra, ikke fra undermodulerne.
"""

from .base import (
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingProvider,
    EmbeddingUnavailableError,
    ProviderInfo,
)
from .chunking import ChunkingConfig, TextChunk, chunk_document
from .factory import build_embedding_provider, get_embedding_provider, reset_embedding_provider
from .hashing import HashingEmbeddingProvider
from .local import LocalEmbeddingProvider
from .remote import ApiEmbeddingProvider
from .service import EmbeddingIndexer, IndexingReport, chunking_config_from_settings

__all__ = [
    "ApiEmbeddingProvider",
    "ChunkingConfig",
    "EmbeddingDimensionError",
    "EmbeddingError",
    "EmbeddingIndexer",
    "EmbeddingProvider",
    "EmbeddingUnavailableError",
    "HashingEmbeddingProvider",
    "IndexingReport",
    "LocalEmbeddingProvider",
    "ProviderInfo",
    "TextChunk",
    "build_embedding_provider",
    "chunk_document",
    "chunking_config_from_settings",
    "get_embedding_provider",
    "reset_embedding_provider",
]

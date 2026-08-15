"""Valg af embedding-udbyder.

Ét sted træffes valget, og det træffes eksplicit. Der falder **aldrig**
automatisk tilbage fra én udbyder til en anden: et halvt indeks bygget
med `local` og et halvt med `hashing` ville give resultater ingen kunne
forklare, og fejlen ville først vise sig som dårlig søgekvalitet.

Samme princip som `retsinformation/factory.py`, hvor fixture-klienten
heller ikke må træde stille til.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

from .base import EmbeddingProvider, EmbeddingUnavailableError
from .hashing import HashingEmbeddingProvider
from .local import LocalEmbeddingProvider
from .remote import ApiEmbeddingProvider

logger = get_logger(__name__)

__all__ = ["build_embedding_provider", "get_embedding_provider", "reset_embedding_provider"]

_VALID = ("local", "api", "hashing")


def build_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """Bygger udbyderen ud fra konfigurationen. Ingen caching."""
    cfg = settings or get_settings()
    name = (cfg.embedding_provider or "").strip().lower()

    if not cfg.embeddings_enabled:
        raise EmbeddingUnavailableError(
            "Vektorlaget er slået fra (EMBEDDINGS_ENABLED=false). Søgningen "
            "kører leksikalsk. Sæt EMBEDDINGS_ENABLED=true for at bruge det."
        )

    if name == "local":
        return LocalEmbeddingProvider(
            model_name=cfg.embedding_model,
            dimensions=cfg.embedding_dimensions,
            query_prefix=cfg.embedding_query_prefix,
            passage_prefix=cfg.embedding_passage_prefix,
            batch_size=cfg.embedding_batch_size,
            torch_threads=cfg.embedding_torch_threads,
            cache_folder=str(cfg.embedding_dir),
        )

    if name == "api":
        return ApiEmbeddingProvider(
            url=cfg.embedding_api_url or "",
            model_name=cfg.embedding_model,
            dimensions=cfg.embedding_dimensions,
            api_key=cfg.embedding_api_key,
            timeout_seconds=cfg.embedding_api_timeout_seconds,
            max_retries=cfg.embedding_api_max_retries,
            batch_size=cfg.embedding_batch_size,
            query_prefix=cfg.embedding_query_prefix,
            passage_prefix=cfg.embedding_passage_prefix,
        )

    if name == "hashing":
        # Lovligt valg, men aldrig et tavst et. Den der læser loggen skal
        # kunne se hvorfor "betydningssøgning" ikke finder synonymer.
        logger.warning(
            "embedding.provider.non_semantic",
            extra={
                "provider": "hashing",
                "note": (
                    "Deterministisk hash uden semantik. Kun til test og fejlsøgning."
                ),
            },
        )
        return HashingEmbeddingProvider(
            dimensions=cfg.embedding_dimensions,
            model_name=cfg.embedding_model if cfg.embedding_model.startswith("hashing") else "hashing-v1",
        )

    raise EmbeddingUnavailableError(
        f"Ukendt EMBEDDING_PROVIDER={cfg.embedding_provider!r}. Vælg en af: {', '.join(_VALID)}."
    )


@lru_cache(maxsize=1)
def _cached_provider() -> EmbeddingProvider:
    return build_embedding_provider()


def get_embedding_provider() -> EmbeddingProvider:
    """Delt udbyder. Den lokale model indlæses kun én gang pr. proces."""
    return _cached_provider()


def reset_embedding_provider() -> None:
    """Rydder cachen. Bruges af test og efter konfigurationsændringer."""
    _cached_provider.cache_clear()

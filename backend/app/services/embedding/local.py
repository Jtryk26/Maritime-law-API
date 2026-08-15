"""Lokal embedding-model i containeren (sentence-transformers).

Standardvalget. Modellen ligger i imaget, så systemet kan vektorisere
uden netværk, uden API-nøgle og uden at sende dansk lovgivning til en
tredjepart.

Modelvalget
===========
``intfloat/multilingual-e5-small``:

* flersproget, dansk indgår i træningsdata,
* 384 dimensioner — en fjerdedel af pladsen sammenlignet med de store,
* kører på CPU i en almindelig container.

E5 er trænet asymmetrisk: tekst der indekseres præfikses ``passage: ``,
og en søgning præfikses ``query: ``. Uden præfikserne falder kvaliteten
mærkbart. De sidder i konfigurationen (`EMBEDDING_QUERY_PREFIX` /
`EMBEDDING_PASSAGE_PREFIX`), så en anden model kan sætte dem tomme.

Indlæsning
==========
``sentence_transformers`` importeres først når modellen faktisk skal
bruges. Dermed kan resten af backenden — API, import, leksikalsk søgning
— køre i et miljø hvor torch slet ikke er installeret. Manglende pakke
giver :class:`EmbeddingUnavailableError` med en besked der siger hvad der
skal installeres, ikke en ImportError langt inde i en søgning.
"""

from __future__ import annotations

import threading

import numpy as np

from app.core.logging import get_logger
from app.core.vectors import normalize

from .base import EmbeddingDimensionError, EmbeddingUnavailableError, ProviderInfo

logger = get_logger(__name__)

__all__ = ["LocalEmbeddingProvider"]


def _suggested_floor(model_name: str) -> float:
    """Startgrænse for hvad der tælles som et semantisk hit.

    E5-modellerne er anisotrope: to tilfældige tekster ligner typisk
    hinanden 0,70–0,75, og relevante par ligger 0,80 og opefter. Uden en
    grænse ville enhver søgning give et fuldt resultatsæt, og listen over
    "søgninger uden svar" ville altid være tom.

    For modeller vi ikke kender skalaen på returneres 0,0 — altså ingen
    grænse. Et gæt ville enten kassere alt eller intet, og det ville være
    umuligt at gennemskue hvorfor.
    """
    lowered = model_name.lower()
    if "e5" in lowered:
        return 0.75
    return 0.0


class LocalEmbeddingProvider:
    """sentence-transformers på CPU."""

    def __init__(
        self,
        model_name: str,
        dimensions: int,
        *,
        query_prefix: str = "",
        passage_prefix: str = "",
        batch_size: int = 16,
        torch_threads: int = 0,
        cache_folder: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._dimensions = dimensions
        self._query_prefix = query_prefix
        self._passage_prefix = passage_prefix
        self._batch_size = max(1, batch_size)
        self._torch_threads = torch_threads
        self._cache_folder = cache_folder
        self._model = None
        # Modellen er ikke trådsikker under indlæsning. Uvicorn kan sagtens
        # sende to søgninger ind samtidig, og to samtidige indlæsninger af
        # den samme model er både spild og en kilde til mærkelige fejl.
        self._lock = threading.Lock()

        self.info = ProviderInfo(
            provider="local",
            model=model_name,
            dimensions=dimensions,
            semantic=True,
            description=f"sentence-transformers/{model_name} på CPU i backend-containeren.",
            suggested_min_similarity=_suggested_floor(model_name),
        )

    # -- Modelindlæsning ----------------------------------------------------

    def _load(self):
        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:  # en anden tråd nåede det først
                return self._model

            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - afhænger af miljø
                raise EmbeddingUnavailableError(
                    "sentence-transformers er ikke installeret. Installér "
                    "backend/requirements-embedding.txt, eller sæt "
                    "EMBEDDING_PROVIDER=api eller EMBEDDINGS_ENABLED=false."
                ) from exc

            if self._torch_threads > 0:  # pragma: no cover - miljøafhængigt
                try:
                    import torch

                    torch.set_num_threads(self._torch_threads)
                except Exception:  # noqa: BLE001 - må aldrig vælte indlæsningen
                    logger.warning("embedding.torch_threads_failed")

            logger.info("embedding.model.loading", extra={"model": self._model_name})
            try:
                model = SentenceTransformer(
                    self._model_name,
                    device="cpu",
                    cache_folder=self._cache_folder,
                )
            except Exception as exc:  # noqa: BLE001 - netværk, disk, filnavn ...
                raise EmbeddingUnavailableError(
                    f"Kunne ikke indlæse embedding-modellen {self._model_name!r}: {exc}"
                ) from exc

            actual = int(model.get_sentence_embedding_dimension() or 0)
            if actual and actual != self._dimensions:
                raise EmbeddingDimensionError(
                    f"Modellen {self._model_name!r} giver {actual} dimensioner, men "
                    f"EMBEDDING_DIMENSIONS er {self._dimensions}. Ret indstillingen og "
                    "genopbyg vektorindekset (python -m app.cli embed run --reset)."
                )

            self._model = model
            logger.info(
                "embedding.model.ready",
                extra={"model": self._model_name, "dimensions": actual or self._dimensions},
            )
            return self._model

    def warmup(self) -> None:
        """Indlæser modellen nu frem for ved første søgning."""
        self._load()

    # -- Kontrakt -----------------------------------------------------------

    def _encode(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        vectors = model.encode(
            texts,
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        array = np.asarray(vectors, dtype=np.float32)
        if array.ndim == 1:
            array = array.reshape(1, -1)
        if array.shape[1] != self._dimensions:
            raise EmbeddingDimensionError(
                f"Modellen gav {array.shape[1]} dimensioner, forventet {self._dimensions}."
            )
        return array

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dimensions), dtype=np.float32)
        return self._encode([f"{self._passage_prefix}{t}" for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        vectors = self._encode([f"{self._query_prefix}{text or ''}"])
        return normalize(vectors[0])

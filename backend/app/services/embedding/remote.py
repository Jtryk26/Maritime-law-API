"""Embedding via et OpenAI-kompatibelt HTTP-endpoint.

Alternativ til den lokale model. Vælges med ``EMBEDDING_PROVIDER=api``
og kræver ``EMBEDDING_API_URL``; nøgle kun hvis udbyderen forlanger det.
Ingen udbyder er hardcodet — URL'en sættes i miljøet, præcis som
Retsinformation-konnektoren gør det, og af samme grund: et gættet
endpoint ville ligne en færdig integration.

Robusthed følger samme mønster som `retsinformation/production.py`:
timeout, eksponentiel backoff på midlertidige fejl, ingen gentagelse af
permanente fejl (4xx bortset fra 429).

Bemærk at brug af et eksternt endpoint sender dokumentteksten ud af
huset. For offentligt tilgængelig lovgivning er det uproblematisk, men
det er et bevidst valg — den lokale model er standarden.
"""

from __future__ import annotations

import time

import httpx
import numpy as np

from app.core.logging import get_logger
from app.core.vectors import normalize

from .base import EmbeddingDimensionError, EmbeddingUnavailableError, ProviderInfo

logger = get_logger(__name__)

__all__ = ["ApiEmbeddingProvider"]

#: Statuskoder der kan give mening at forsøge igen.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class ApiEmbeddingProvider:
    """Kalder et eksternt embeddings-endpoint."""

    def __init__(
        self,
        url: str,
        model_name: str,
        dimensions: int,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        batch_size: int = 16,
        query_prefix: str = "",
        passage_prefix: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        if not url:
            raise EmbeddingUnavailableError(
                "EMBEDDING_API_URL er ikke sat. Sæt den, eller vælg "
                "EMBEDDING_PROVIDER=local."
            )
        self._url = url
        self._model_name = model_name
        self._dimensions = dimensions
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._batch_size = max(1, batch_size)
        self._query_prefix = query_prefix
        self._passage_prefix = passage_prefix
        self._client = client

        self.info = ProviderInfo(
            provider="api",
            model=model_name,
            dimensions=dimensions,
            semantic=True,
            description=f"Eksternt embeddings-endpoint: {url}",
        )

    # -- HTTP ---------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _post(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self._model_name, "input": texts}
        client = self._client or httpx.Client(timeout=self._timeout)
        owns_client = self._client is None

        try:
            last_error: Exception | None = None
            for attempt in range(1, self._max_retries + 1):
                try:
                    response = client.post(self._url, json=payload, headers=self._headers())
                except httpx.HTTPError as exc:
                    last_error = exc
                else:
                    if response.status_code < 400:
                        return _extract_vectors(response.json())
                    if response.status_code not in _RETRYABLE_STATUS:
                        # Permanent fejl. Flere forsøg ændrer ingenting.
                        raise EmbeddingUnavailableError(
                            f"Embedding-endpointet svarede {response.status_code}: "
                            f"{response.text[:300]}"
                        )
                    last_error = EmbeddingUnavailableError(
                        f"Embedding-endpointet svarede {response.status_code}"
                    )

                if attempt < self._max_retries:
                    delay = 2.0 ** (attempt - 1)
                    logger.warning(
                        "embedding.api.retry",
                        extra={"attempt": attempt, "delay": delay, "error": str(last_error)},
                    )
                    time.sleep(delay)

            raise EmbeddingUnavailableError(
                f"Embedding-endpointet kunne ikke nås efter {self._max_retries} forsøg: "
                f"{last_error}"
            )
        finally:
            if owns_client:
                client.close()

    # -- Kontrakt -----------------------------------------------------------

    def _encode(self, texts: list[str]) -> np.ndarray:
        rows: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            rows.extend(self._post(texts[start : start + self._batch_size]))

        array = np.asarray(rows, dtype=np.float32)
        if array.ndim == 1:
            array = array.reshape(1, -1)
        if array.shape[0] != len(texts):
            raise EmbeddingUnavailableError(
                f"Endpointet returnerede {array.shape[0]} vektorer for {len(texts)} tekster."
            )
        if array.shape[1] != self._dimensions:
            raise EmbeddingDimensionError(
                f"Endpointet gav {array.shape[1]} dimensioner, forventet {self._dimensions}."
            )
        # Udbydere normaliserer ikke nødvendigvis selv.
        return np.vstack([normalize(row) for row in array]).astype(np.float32)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dimensions), dtype=np.float32)
        return self._encode([f"{self._passage_prefix}{t}" for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._encode([f"{self._query_prefix}{text or ''}"])[0]


def _extract_vectors(payload: dict) -> list[list[float]]:
    """Læser vektorerne ud af et OpenAI-formet svar.

    Tolerant på samme måde som `discovery/extract.py`: vi leder efter
    strukturen frem for at kræve præcise feltnavne, fordi kompatible
    udbydere afviger i detaljer.
    """
    if not isinstance(payload, dict):
        raise EmbeddingUnavailableError("Uventet svar fra embedding-endpointet (ikke JSON-objekt).")

    data = payload.get("data")
    if isinstance(data, list) and data:
        vectors = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("embedding"), list):
                vectors.append([float(v) for v in item["embedding"]])
            elif isinstance(item, list):
                vectors.append([float(v) for v in item])
        if vectors:
            return vectors

    # Nogle selvhostede servere svarer {"embeddings": [[...], [...]]}.
    embeddings = payload.get("embeddings")
    if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
        return [[float(v) for v in row] for row in embeddings]

    raise EmbeddingUnavailableError(
        "Kunne ikke finde vektorer i svaret. Forventede enten 'data[].embedding' "
        f"eller 'embeddings[][]'. Nøgler i svaret: {sorted(payload)[:10]}"
    )

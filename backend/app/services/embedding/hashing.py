"""Deterministisk hash-udbyder — til test og til drift uden model.

VIGTIGT: denne udbyder er **ikke semantisk**. Den projicerer ordene i
teksten ned i et fast antal dimensioner med en stabil hashfunktion.
To tekster der deler ord får derfor høj lighed; to tekster der betyder
det samme med andre ord får det ikke. "Redningsflåde" og "livbåd" er
fremmede for den.

Hvorfor findes den så?

* Hele testpakken kan køre uden at hente en 500 MB model, og resultaterne
  er bit-for-bit reproducerbare på tværs af maskiner.
* Rørføringen — chunking, lagring, sammensmeltning, paginering, søgelog —
  kan afprøves uafhængigt af modelkvalitet.

Den må aldrig blive et stille fald-tilbage. `factory.py` kræver at den
vælges eksplicit, og `ProviderInfo.semantic` er falsk, så både
`embed status`, /api/stats og brugerfladen kan sige det højt.
"""

from __future__ import annotations

import hashlib

import numpy as np

from app.core.text import fold, tokenize
from app.core.vectors import normalize

from .base import ProviderInfo

__all__ = ["HashingEmbeddingProvider"]


class HashingEmbeddingProvider:
    """Hashet bag-of-words. Stabil, hurtig, uden semantik."""

    def __init__(self, dimensions: int = 384, *, model_name: str = "hashing-v1") -> None:
        if dimensions <= 0:
            raise ValueError("dimensions skal være positiv")
        self._dimensions = dimensions
        self.info = ProviderInfo(
            provider="hashing",
            model=model_name,
            dimensions=dimensions,
            semantic=False,
            description=(
                "Deterministisk hash af ord. Finder IKKE synonymer eller "
                "beslægtede formuleringer. Kun til test og fejlsøgning."
            ),
            # 0,0 — altså ingen grænse. Målt på fixturmaterialet giver en
            # meningsløs søgning ("kvantemekanisk vandpolo") højere lighed
            # end en rigtig ("brand passagerskib"), fordi hashen kun ser
            # ordsammenfald og et kort søgeudtryk fortyndes mod et langt
            # stykke lovtekst. Enhver grænse ville derfor kassere det
            # rigtige og beholde det forkerte. Det er netop den forskel
            # mellem denne udbyder og en rigtig model, som `semantic=False`
            # advarer om.
            suggested_min_similarity=0.0,
        )

    # -- Intern -------------------------------------------------------------

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self._dimensions, dtype=np.float32)
        tokens = tokenize(fold(text or ""))
        if not tokens:
            return vector

        # Unikke tokens vægtes efter hyppighed med en logaritmisk dæmpning,
        # så et ord der står 200 gange ikke overdøver resten. Samme
        # betragtning som i relevansmotorens frekvensloft.
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1

        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self._dimensions
            # Fortegnet fra en anden del af hashen, så forskellige ord i
            # samme spand ikke systematisk lægger sig oveni hinanden.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * float(1.0 + np.log1p(count))

        return normalize(vector)

    # -- Kontrakt -----------------------------------------------------------

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dimensions), dtype=np.float32)
        return np.vstack([self._vector(t) for t in texts]).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)

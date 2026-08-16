"""Rate limiting af indgående forespørgsler.

Bemærk forskellen på denne og `app.core.ratelimit`: dén begrænser *vores*
kald **ud** til Retsinformation. Denne begrænser *andres* kald **ind** til
os.

Modellen er et glidende vindue pr. klient: tidsstemplerne for de seneste
kald gemmes, og alt ældre end vinduet kasseres. Det koster lidt mere
hukommelse end en simpel tæller pr. minut, men undgår den kendte svaghed
ved faste vinduer, hvor en klient kan sende dobbelt kvote hen over et
vinduesskift.

Tælleren lever i processen. Systemet kører i én backend-container, så det
er tilstrækkeligt. Skulle der senere komme flere replikaer, skal
`SlidingWindowLimiter` udskiftes med en Redis-baseret implementering —
grænsefladen (`check`) er holdt lille netop af den grund.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

__all__ = ["Decision", "SlidingWindowLimiter"]


@dataclass(frozen=True, slots=True)
class Decision:
    """Svaret på "må denne forespørgsel køre?"."""

    allowed: bool
    limit: int
    remaining: int
    #: Sekunder til den ældste registrering falder ud af vinduet.
    retry_after: int


class SlidingWindowLimiter:
    """Højst `limit` kald pr. `window_seconds` pr. nøgle.

    Trådsikker: uvicorn afvikler synkrone endepunkter i en trådpulje, så
    flere forespørgsler rammer tælleren samtidig.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        *,
        max_keys: int = 20000,
    ) -> None:
        self._limit = max(int(limit), 0)
        self._window = max(float(window_seconds), 1.0)
        self._max_keys = max(int(max_keys), 1)
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    def check(self, key: str, *, now: float | None = None) -> Decision:
        """Registrerer et kald og fortæller om det må gennemføres."""
        if self._limit <= 0:  # 0 = slået fra
            return Decision(True, self._limit, self._limit, 0)

        now = time.monotonic() if now is None else now
        cutoff = now - self._window

        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
            self._hits.move_to_end(key)

            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= self._limit:
                retry_after = max(1, int(hits[0] + self._window - now) + 1)
                return Decision(False, self._limit, 0, retry_after)

            hits.append(now)
            self._prune_locked(cutoff)
            return Decision(True, self._limit, self._limit - len(hits), 0)

    def _prune_locked(self, cutoff: float) -> None:
        """Holder hukommelsesforbruget nede.

        Tomme nøgler fjernes, og skulle antallet af klienter alligevel
        vokse over loftet, ryger de længst uberørte ud. Uden dette ville
        selve rate limiteren være en angrebsflade: mange forskellige
        afsenderadresser ville kunne fylde hukommelsen.
        """
        while self._hits:
            oldest_key = next(iter(self._hits))
            hits = self._hits[oldest_key]
            if hits and hits[-1] > cutoff and len(self._hits) <= self._max_keys:
                break
            self._hits.popitem(last=False)

    def reset(self) -> None:
        """Bruges af tests."""
        with self._lock:
            self._hits.clear()

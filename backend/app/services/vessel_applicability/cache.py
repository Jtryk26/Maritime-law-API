"""Cache for færdige vurderinger.

Nøglen er ``(regelsættets fingeraftryk, profilens hash)``. Fingeraftrykket er
en hash over motorversionen og hver godkendt regels ``(rule_id,
content_hash)``, så en godkendelse ændrer nøglen for **alle** vurderinger.
Det er korrekt, ikke groft: hver vurdering kører mod hele det godkendte
regelsæt, så en ny godkendt regel påvirker hver eneste af dem.

Ugyldiggørelse sker derfor to steder:

* ``invalidate_all`` kaldes af godkendelsen i den proces, der godkender, så
  hukommelsen frigives med det samme.
* I alle andre processer kan en gammel post ikke rammes, fordi
  fingeraftrykket læses fra databasen ved hver forespørgsel. Der er ingen
  delt tilstand at holde synkron, og den offentlige rute behøver ingen
  skriveadgang til databasen — den kører som ren læser i produktion.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Generic, TypeVar

__all__ = ["EvaluationCache", "evaluation_cache"]

T = TypeVar("T")


class EvaluationCache(Generic[T]):
    """Lille LRU, trådsikker. Lever i processen."""

    def __init__(self, max_entries: int = 512) -> None:
        self._max = max_entries
        self._entries: OrderedDict[tuple[str, str], T] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, fingerprint: str, profile_hash: str) -> T | None:
        key = (fingerprint, profile_hash)
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, fingerprint: str, profile_hash: str, value: T) -> None:
        with self._lock:
            # Poster under et gammelt fingeraftryk kan aldrig rammes igen.
            for stale in [k for k in self._entries if k[0] != fingerprint]:
                del self._entries[stale]
            self._entries[(fingerprint, profile_hash)] = value
            self._entries.move_to_end((fingerprint, profile_hash))
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def invalidate_all(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            return count

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


evaluation_cache: EvaluationCache = EvaluationCache()

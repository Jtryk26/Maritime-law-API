"""Trådsikker klientside-rate limiting.

Retsinformation håndhæver en dokumenteret grænse på høsteservicen (1 kald
pr. 10 sekunder). Søgegrænsefladen på www.retsinformation.dk er en almindelig
webtjeneste uden publiceret grænse; vi begrænser os selv alligevel, fordi en
efterindlæsning af flere tusinde dokumenter ellers ligner et angreb.

Klassen ligger i `core`, fordi to uafhængige klienter bruger den mod to
forskellige værter med hver sit interval.
"""

from __future__ import annotations

import threading
import time

from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["RateLimiter"]


class RateLimiter:
    """Sikrer mindst `min_interval_seconds` mellem to kald.

    Trådsikker, så flere samtidige arbejdere deler ét budget når de deler
    én instans.
    """

    def __init__(self, min_interval_seconds: float, *, name: str = "http") -> None:
        self._min_interval = max(float(min_interval_seconds), 0.0)
        self._name = name
        self._last_call: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            if self._last_call is not None:
                remaining = self._min_interval - (time.monotonic() - self._last_call)
                if remaining > 0:
                    logger.debug(
                        "ratelimit.wait",
                        extra={"client": self._name, "seconds": round(remaining, 1)},
                    )
                    time.sleep(remaining)
            self._last_call = time.monotonic()

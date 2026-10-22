"""Bounded official HTTPS requests. WAF/authorization failures are terminal.

No challenge solving, browser impersonation, token replay, alternate proxies,
URL guessing after denial, or silent fixture fallback. Redirects remain within
Cellar, never through the EUR-Lex interactive website.
"""

from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.services.retsinformation.base import PermanentSourceError, TransientSourceError

MAX_BYTES = 12 * 1024 * 1024
CELLAR_HOST = "publications.europa.eu"


class CellarTransport:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=30, follow_redirects=False,
            headers={"User-Agent": "Solov-EURLex-M3/1.0 (curated legal research)"})
        self.owns_client = client is None

    def get(self, url: str, *, accept: str, language: str = "en") -> tuple[bytes, str, str]:
        for _ in range(6):
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.hostname != CELLAR_HOST
                    or parsed.username or parsed.password or parsed.port not in (None, 443)):
                raise PermanentSourceError("Cellar redirect is outside the approved HTTPS service")
            try:
                with self.client.stream("GET", url, headers={"Accept": accept, "Accept-Language": {"en": "eng", "da": "dan"}.get(language, language)},
                                        follow_redirects=False) as response:
                    # AWS WAF can respond 202 even though no content was supplied.
                    if response.status_code in {202, 401, 403} or response.headers.get("x-amzn-waf-action"):
                        raise PermanentSourceError("Cellar access denied/challenged; stop and use an approved service agreement")
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise PermanentSourceError("Cellar redirect has no location")
                        url = urljoin(url, location)
                        target = urlsplit(url)
                        # Cellar's documented service emits canonical HTTP
                        # identifiers in Location. Keep the same official
                        # resource but upgrade transport to HTTPS before I/O.
                        if (target.scheme == "http" and target.hostname == CELLAR_HOST
                                and not target.username and not target.password
                                and target.port in (None, 80) and target.path.startswith("/resource/cellar/")):
                            url = urlunsplit(("https", CELLAR_HOST, target.path, target.query, target.fragment))
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        raise TransientSourceError(f"Cellar HTTP {response.status_code}; defer acquisition")
                    if response.status_code != 200:
                        raise PermanentSourceError(f"Cellar HTTP {response.status_code}")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise PermanentSourceError("Cellar response exceeds acquisition limit")
                    return bytes(body), str(response.url), response.headers.get("content-type", "").split(";")[0].lower()
            except httpx.HTTPError as exc:
                raise TransientSourceError(f"Cellar transport failed ({type(exc).__name__})") from exc
        raise PermanentSourceError("Cellar redirect limit exceeded")

    def close(self):
        if self.owns_client:
            self.client.close()

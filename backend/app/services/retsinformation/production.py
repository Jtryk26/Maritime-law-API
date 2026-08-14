"""Produktionsklient mod Retsinformations officielle høsteservice.

VERIFICERET GRUNDLAG
====================
Denne klient er udelukkende bygget på officiel dokumentation:

  "Retsinformation høsteservice — REST API vejledning", version 1.3,
  revideret 13.09.2023, Civilstyrelsen/Schultz.
  https://www.retsinformation.dk/offentlig/vejledning/Retsinformation%20REST%20API%20vejledning.pdf

Verificerede forhold:

  * Base-URL:        https://api.retsinformation.dk
  * Endpoint:        GET /v1/Documents
  * Parameter:       date=YYYY-MM-DD (højst 10 kalenderdage tilbage)
  * Uden parameter:  ændrede/tilføjede/fjernede dokumenter det seneste døgn
                     med skæringstidspunkt kl. 03:00.
  * Autentifikation: ingen.
  * Åbningstid:      03:00–23:45. Kald udenfor giver HTTP 400.
  * Rate limit:      højst 1 kald pr. 10 sekunder. Overskridelse giver HTTP 429.
  * Svarformat:      JSON-array med felterne documentId, accessionsnummer,
                     reasonForChange, changeDate, documentType{shortName,id},
                     href, images[].
  * Fuldtekst:       href peger på ELI-XML,
                     https://www.retsinformation.dk/eli/accn/{accn}/xml

KENDTE BEGRÆNSNINGER — LÆS DETTE
=================================
1. Høsteservicen er en ÆNDRINGSFEED, ikke et katalog. Der findes ikke i den
   officielle dokumentation et endpoint der lister hele lovsamlingen eller
   tillader fritekstsøgning. Man kan derfor ikke lave en fuld historisk
   backfill af al maritim lovgivning via dette API alene. Systemet opbygger
   sin database over tid ved at køre importen dagligt, eller ved at et
   dokument-ID tilføjes eksplicit (se `get_documents(explicit_ids=...)`).

2. ELI-XML-skemaet er ikke formelt publiceret på en form der kunne
   verificeres. `xml_parser` er derfor bevidst tolerant: den leder efter
   kendte elementnavne og falder tilbage til at udtrække al tekst.
   Se `xml_parser.py`.

3. Ændringsfeeden blev verificeret mod det aktive live-endpoint
   13.08.2026 og returnerede det dokumenterede JSON-format. Den første fulde
   produktionsimport bør fortsat overvåges, især ELI-XML-parsningen på tværs
   af dokumenttyper. `FixtureRetsinformationClient` dækker offline tests.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.ratelimit import RateLimiter
from app.core.text import normalize_whitespace

from .base import (
    DocumentNotFoundError,
    DocumentRef,
    NormalizedDocument,
    PermanentSourceError,
    TransientSourceError,
)
from .normalization import (
    map_document_type,
    map_status,
    parse_danish_date,
    resolve_authority,
)
from .xml_parser import ParsedDocumentXml, parse_document_xml

logger = get_logger(__name__)

#: Officiel grænse: datoen må højst ligge 10 kalenderdage tilbage.
MAX_LOOKBACK_DAYS = 10

#: Officiel åbningstid for høsteservicen (lokal dansk tid).
SERVICE_OPEN_HOUR = 3
SERVICE_CLOSE_HOUR = 23
SERVICE_CLOSE_MINUTE = 45


class ProductionRetsinformationClient:
    """Klient mod den officielle høsteservice.

    Opfylder :class:`~app.services.retsinformation.base.SourceClient`.
    """

    kind = "production"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        document_base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        min_request_interval: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.retsinformation_base_url).rstrip("/")
        self.document_base_url = (
            document_base_url or settings.retsinformation_document_base_url
        ).rstrip("/")
        self.max_retries = (
            max_retries if max_retries is not None else settings.retsinformation_max_retries
        )
        timeout_value = (
            timeout if timeout is not None else settings.retsinformation_timeout_seconds
        )
        interval = (
            min_request_interval
            if min_request_interval is not None
            else settings.retsinformation_min_request_interval_seconds
        )
        self._rate_limiter = RateLimiter(interval, name="retsinformation-harvest")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_value),
            headers={
                "User-Agent": settings.retsinformation_user_agent,
                "Accept": "application/json",
            },
            follow_redirects=True,
        )

    # -- HTTP ---------------------------------------------------------------

    def _request(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        """Udfører et HTTP-kald med rate limiting, retry og backoff.

        Permanente fejl (4xx bortset fra 429) forsøges ikke igen.
        """
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            self._rate_limiter.wait()
            try:
                response = self._client.get(url, params=params)
            except httpx.TimeoutException as exc:
                last_error = TransientSourceError(f"Timeout ved {url}: {exc}")
            except httpx.HTTPError as exc:
                last_error = TransientSourceError(f"Netværksfejl ved {url}: {exc}")
            else:
                status = response.status_code

                if status == 404:
                    raise DocumentNotFoundError(f"Ikke fundet: {url}")

                if status == 429:
                    # Kilden har en dokumenteret grænse på 1 kald/10 sek.
                    retry_after = _parse_retry_after(response) or 10.0
                    last_error = TransientSourceError("Rate limit (429) fra Retsinformation")
                    logger.warning(
                        "retsinformation.ratelimited",
                        extra={"url": url, "retry_after": retry_after, "attempt": attempt},
                    )
                    time.sleep(retry_after)
                    continue

                if status == 400:
                    # Dokumenteret: kald udenfor åbningstiden 03:00–23:45 giver 400.
                    # Dette er en PLANLAGT, forbigående tilstand — ikke en
                    # permanent fejl. Rejses derfor som TransientSourceError,
                    # så posten sættes i RETRY frem for at blive opgivet
                    # endeligt. Bemærk: standard-backoff (5/15/45 min, 3
                    # forsøg) rækker ikke over hele det 3 timer og 15
                    # minutter lange lukkevindue — poster der først fejler
                    # sent på aftenen kan stadig ende i FAILED før kl. 03:00.
                    # Kør `backfill enqueue --requeue-failed` om morgenen,
                    # eller hæv --max-attempts for kørsler der forventes at
                    # strække sig hen over lukketid.
                    if not _within_service_hours():
                        last_error = TransientSourceError(
                            f"HTTP 400 fra {url}. Høsteservicen har åbningstid "
                            "03:00–23:45; kaldet ramte det lukkede vindue. "
                            "Forsøges igen."
                        )
                        logger.info(
                            "retsinformation.closed_hours",
                            extra={"url": url, "attempt": attempt},
                        )
                        if attempt < self.max_retries:
                            backoff = min(2 ** (attempt - 1) * 2.0, 30.0)
                            time.sleep(backoff)
                        continue
                    raise PermanentSourceError(f"HTTP 400 fra {url}.")

                if 400 <= status < 500:
                    raise PermanentSourceError(f"HTTP {status} fra {url}")

                if status >= 500:
                    last_error = TransientSourceError(f"HTTP {status} fra {url}")
                else:
                    return response

            if attempt < self.max_retries:
                backoff = min(2 ** (attempt - 1) * 2.0, 30.0)
                logger.warning(
                    "retsinformation.retry",
                    extra={"url": url, "attempt": attempt, "backoff": backoff,
                           "error": str(last_error)},
                )
                time.sleep(backoff)

        raise last_error or TransientSourceError(f"Kaldet til {url} mislykkedes")

    # -- Listeoperationer ---------------------------------------------------

    def get_documents(
        self,
        *,
        since: date | None = None,
        explicit_ids: Iterable[str] | None = None,
    ) -> list[DocumentRef]:
        """Returnerer dokumenter til behandling.

        Høsteservicen er en ændringsfeed. Uden `since` returneres det
        seneste døgns ændringer. `explicit_ids` gør det muligt at hente
        bestemte accessionsnumre direkte, hvilket er den eneste måde at
        efterindlæse ældre dokumenter på via de officielle grænseflader.
        """
        refs: list[DocumentRef] = []

        if explicit_ids:
            for accn in explicit_ids:
                refs.append(
                    DocumentRef(
                        source_id=accn,
                        retsinformation_id=accn,
                        source_url=self._eli_url(accn),
                        reason_for_change="ExplicitRequest",
                    )
                )
            return refs

        if since is not None:
            return self.get_updated_documents(since)

        return self._fetch_change_feed(None)

    def get_updated_documents(self, since: date) -> list[DocumentRef]:
        """Henter ændrede dokumenter fra `since` og frem.

        Kilden tillader højst 10 kalenderdage tilbage og behandler én dag
        pr. kald, så der kaldes én gang pr. dag i intervallet.
        """
        today = datetime.now(timezone.utc).date()
        earliest = today - timedelta(days=MAX_LOOKBACK_DAYS)

        if since < earliest:
            logger.warning(
                "retsinformation.lookback.clamped",
                extra={"requested": since.isoformat(), "earliest": earliest.isoformat()},
            )
            since = earliest

        seen: set[str] = set()
        refs: list[DocumentRef] = []
        cursor = since
        while cursor <= today:
            for ref in self._fetch_change_feed(cursor):
                if ref.source_id not in seen:
                    seen.add(ref.source_id)
                    refs.append(ref)
            cursor += timedelta(days=1)
        return refs

    def _fetch_change_feed(self, day: date | None) -> list[DocumentRef]:
        url = f"{self.base_url}/v1/Documents"
        params = {"date": day.isoformat()} if day else None
        response = self._request(url, params=params)

        try:
            payload = response.json()
        except ValueError as exc:
            raise PermanentSourceError(f"Ugyldigt JSON fra {url}: {exc}") from exc

        if not isinstance(payload, list):
            raise PermanentSourceError(
                f"Uventet svarformat fra {url}: forventede en liste, fik {type(payload).__name__}"
            )

        refs = [self._to_ref(item) for item in payload if isinstance(item, dict)]
        logger.info(
            "retsinformation.feed.fetched",
            extra={"date": day.isoformat() if day else "seneste-doegn", "count": len(refs)},
        )
        return refs

    def _to_ref(self, item: dict[str, Any]) -> DocumentRef:
        """Oversætter en feed-post til systemets referencetype."""
        accn = item.get("accessionsnummer") or item.get("documentId") or ""
        doc_type = item.get("documentType") or {}
        short_name = doc_type.get("shortName") if isinstance(doc_type, dict) else None
        href = item.get("href") or (self._eli_url(accn) if accn else None)

        return DocumentRef(
            source_id=str(accn),
            document_type=map_document_type(short_name),
            source_url=href,
            retsinformation_id=str(accn) if accn else None,
            change_date=parse_danish_date(item.get("changeDate")),
            reason_for_change=item.get("reasonForChange"),
            raw=item,
        )

    # -- Dokumentoperationer ------------------------------------------------

    def _eli_url(self, accession_number: str, *, xml: bool = False) -> str:
        """Bygger ELI-URL. Formatet er dokumenteret af Civilstyrelsen:
        https://www.retsinformation.dk/eli/accn/{accn}[/xml]
        """
        suffix = "/xml" if xml else ""
        return f"{self.document_base_url}/eli/accn/{accession_number}{suffix}"

    def _fetch_xml(self, document_id: str) -> ParsedDocumentXml:
        url = self._eli_url(document_id, xml=True)
        response = self._request(url)
        return parse_document_xml(response.text)

    def get_document(self, document_id: str) -> NormalizedDocument:
        """Henter og normaliserer et komplet dokument."""
        parsed = self._fetch_xml(document_id)
        return self._normalize(document_id, parsed)

    def get_document_metadata(self, document_id: str) -> NormalizedDocument:
        """Henter dokumentet uden brødtekst.

        Kilden udstiller ikke et separat metadata-endpoint, så samme
        XML hentes og brødteksten kasseres.
        """
        parsed = self._fetch_xml(document_id)
        doc = self._normalize(document_id, parsed)
        doc.content = ""
        return doc

    def get_document_text(self, document_id: str) -> str:
        """Henter den fulde lovtekst som ren tekst."""
        return self._fetch_xml(document_id).content

    def _normalize(self, document_id: str, parsed: ParsedDocumentXml) -> NormalizedDocument:
        """Oversætter parset XML til systemets normaliserede form."""
        title = normalize_whitespace(parsed.title) or f"Dokument {document_id}"

        return NormalizedDocument(
            source="retsinformation",
            source_id=document_id,
            title=title,
            content=parsed.content,
            short_title=normalize_whitespace(parsed.short_title) or None,
            document_type=map_document_type(parsed.document_type),
            authority=resolve_authority(parsed.authority, title=title, content=parsed.content),
            published_date=parse_danish_date(parsed.published_date),
            effective_date=parse_danish_date(parsed.effective_date),
            status=map_status(parsed.status),
            source_url=self._eli_url(document_id),
            retsinformation_id=document_id,
            document_number=parsed.document_number,
            is_synthetic=False,
            metadata={
                "ministry": parsed.ministry,
                "keywords": parsed.keywords,
                "document_number": parsed.document_number,
            },
            raw_metadata=parsed.raw_metadata,
            retrieved_at=datetime.now(timezone.utc),
        )

    # -- Livscyklus ---------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "ProductionRetsinformationClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _within_service_hours(now: datetime | None = None) -> bool:
    """Er vi indenfor høsteservicens dokumenterede åbningstid 03:00–23:45?"""
    current = now or datetime.now()
    if current.hour < SERVICE_OPEN_HOUR:
        return False
    if current.hour > SERVICE_CLOSE_HOUR:
        return False
    if current.hour == SERVICE_CLOSE_HOUR and current.minute > SERVICE_CLOSE_MINUTE:
        return False
    return True

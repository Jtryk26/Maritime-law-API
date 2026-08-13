"""Søgeklient mod Retsinformations resultatgrænseflade.

HVAD DER ER VERIFICERET — OG HVAD DER IKKE ER
=============================================
Verificeret (officiel dokumentation, Civilstyrelsen/Schultz, v1.3):

  * Høsteservicen ``https://api.retsinformation.dk/v1/Documents`` er en
    ÆNDRINGSFEED med højst 10 dages tilbagekig. Den kan ikke liste hele
    lovsamlingen og har ingen fritekst- eller myndighedssøgning.
  * ELI-URL-formatet ``https://www.retsinformation.dk/eli/accn/{accn}``.

IKKE verificeret:

  * Retsinformations avancerede søgeside er en JavaScript-applikation.
    Det endpoint, dens frontend kalder for at hente resultatlister, er
    **ikke** en dokumenteret grænseflade, og dets URL, metode og
    parameternavne kunne ikke kontrolleres i det miljø denne klient blev
    skrevet i (ingen udgående netværksadgang til retsinformation.dk).

Derfor står der **ingen URL i denne kode**. Den skal opgives i
konfigurationen, og den findes ved at aflæse ét faktisk kald:

  1. Åbn https://www.retsinformation.dk/documents i en browser.
  2. Åbn udviklerværktøjernes netværksfane.
  3. Udfør søgningen med ``administrerendeMyndighed = Søfartsstyrelsen``.
  4. Aflæs anmodningens URL, metode og parametre.
  5. Sæt dem i ``.env`` (se ``.env.example``).
  6. Kontrollér svaret med::

         python -m app.cli backfill probe-search --out probe.json

Dette er et bevidst valg efter projektets regel om ikke at opfinde
endpoints. En gættet URL ville se ud som en færdig integration og fejle
stille i produktion.

ANMODNINGSSKABELONEN
====================
``RETSINFORMATION_SEARCH_PARAMS`` er et JSON-objekt med pladsholdere::

    {"administrerendeMyndighed": "{authority}",
     "retsinformationStatus": "{status}",
     "page": "{page}", "pageSize": "{page_size}"}

Understøttede pladsholdere: ``{authority}``, ``{status}``, ``{page}``,
``{page_size}``, ``{offset}``. En værdi der *udelukkende* består af en
numerisk pladsholder sendes som tal, ikke som streng. Nøgler hvis værdi
bliver tom (f.eks. ``{status}`` uden statusfilter) udelades.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.ratelimit import RateLimiter

from .base import (
    DiscoveryConfigurationError,
    DiscoveryHit,
    DiscoveryPaginationError,
    DiscoveryQuery,
    DiscoveryResponseError,
    DiscoveryResult,
)
from .extract import extract_hit_fields, find_record_list, find_reported_total

logger = get_logger(__name__)

__all__ = ["RetsinformationSearchClient", "render_request"]

_NUMERIC_PLACEHOLDERS = {"{page}", "{page_size}", "{offset}"}


def render_request(
    template: dict[str, Any],
    *,
    authority: str,
    status: str | None,
    page: int,
    page_size: int,
    offset: int,
) -> dict[str, Any]:
    """Fletter pladsholdere ind i anmodningsskabelonen.

    Tomme værdier fjernes, så en søgning uden statusfilter ikke sender
    ``retsinformationStatus=``, hvilket nogle tjenester tolker som
    "status er tom streng" frem for "intet filter".
    """
    values = {
        "authority": authority,
        "status": status or "",
        "page": page,
        "page_size": page_size,
        "offset": offset,
    }

    def render(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: render(v) for k, v in value.items()}
        if isinstance(value, list):
            return [render(v) for v in value]
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped in _NUMERIC_PLACEHOLDERS:
            return values[stripped[1:-1]]
        try:
            return value.format(**values)
        except (KeyError, IndexError) as exc:
            raise DiscoveryConfigurationError(
                f"Ukendt pladsholder i anmodningsskabelonen: {value!r} ({exc})"
            ) from exc

    rendered = render(template)
    if not isinstance(rendered, dict):
        raise DiscoveryConfigurationError("Anmodningsskabelonen skal være et JSON-objekt.")

    return {k: v for k, v in rendered.items() if v not in ("", None)}


class RetsinformationSearchClient:
    """Henter kandidatlister fra en konfigureret søgegrænseflade.

    Opfylder :class:`~app.services.discovery.base.DiscoveryClient`.
    Klienten kender ikke maritim relevans, databaser eller køer — den
    leverer accessionsnumre og de metadata svaret tilfældigvis bærer.
    """

    kind = "production"

    def __init__(
        self,
        *,
        url: str | None = None,
        method: str | None = None,
        params_template: dict[str, Any] | str | None = None,
        page_size: int | None = None,
        pagination: str | None = None,
        first_page: int | None = None,
        max_pages: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        min_request_interval: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()

        self.url = (url or settings.retsinformation_search_url or "").strip()
        if not self.url:
            raise DiscoveryConfigurationError(
                "RETSINFORMATION_SEARCH_URL er ikke sat.\n"
                "Søgegrænsefladen på www.retsinformation.dk er ikke en dokumenteret\n"
                "del af høsteservicen, så URL'en er bevidst ikke hardkodet.\n"
                "Aflæs ét faktisk søgekald i browserens netværksfane, sæt\n"
                "RETSINFORMATION_SEARCH_URL / _METHOD / _PARAMS i .env, og\n"
                "kontrollér svaret med: python -m app.cli backfill probe-search"
            )

        self.method = (method or settings.retsinformation_search_method).upper()
        if self.method not in {"GET", "POST"}:
            raise DiscoveryConfigurationError(
                f"Understøttede metoder er GET og POST, ikke {self.method!r}."
            )

        raw_template = (
            params_template
            if params_template is not None
            else settings.retsinformation_search_params
        )
        self.params_template = _coerce_template(raw_template)

        self.page_size = page_size or settings.retsinformation_search_page_size
        self.pagination = (pagination or settings.retsinformation_search_pagination).lower()
        if self.pagination not in {"page", "offset"}:
            raise DiscoveryConfigurationError(
                f"Ukendt pagineringsform {self.pagination!r}. Brug 'page' eller 'offset'."
            )
        self.first_page = (
            first_page if first_page is not None else settings.retsinformation_search_first_page
        )
        self.max_pages = max_pages or settings.retsinformation_search_max_pages
        self.max_retries = (
            max_retries if max_retries is not None else settings.retsinformation_max_retries
        )

        interval = (
            min_request_interval
            if min_request_interval is not None
            else settings.retsinformation_search_min_request_interval_seconds
        )
        self._rate_limiter = RateLimiter(interval, name="retsinformation-search")

        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(
                timeout if timeout is not None else settings.retsinformation_timeout_seconds
            ),
            headers={
                "User-Agent": settings.retsinformation_user_agent,
                "Accept": "application/json",
            },
            follow_redirects=True,
        )

    # -- HTTP ---------------------------------------------------------------

    def request_page(
        self, query: DiscoveryQuery, *, page: int, offset: int
    ) -> tuple[dict[str, Any] | list[Any], dict[str, Any]]:
        """Ét kald. Returnerer (afkodet svar, den sendte anmodning).

        Anmodningen returneres med, så ``probe-search`` kan vise præcis
        hvad der blev sendt.
        """
        payload = render_request(
            {**self.params_template, **query.extra},
            authority=query.authority,
            status=query.status,
            page=page,
            page_size=self.page_size,
            offset=offset,
        )

        response = self._request(payload)
        try:
            decoded = response.json()
        except ValueError as exc:
            snippet = response.text[:200].replace("\n", " ")
            raise DiscoveryResponseError(
                f"Svaret fra {self.url} er ikke JSON "
                f"(content-type={response.headers.get('content-type')!r}): {snippet!r}. "
                "Peger RETSINFORMATION_SEARCH_URL på HTML-siden frem for dens "
                "dataendpoint?"
            ) from exc
        return decoded, payload

    def _request(self, payload: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            self._rate_limiter.wait()
            try:
                if self.method == "GET":
                    response = self._client.get(self.url, params=payload)
                else:
                    response = self._client.post(self.url, json=payload)
            except httpx.HTTPError as exc:
                last_error = DiscoveryResponseError(f"Netværksfejl ved {self.url}: {exc}")
            else:
                status = response.status_code
                if status == 429:
                    wait = _retry_after(response) or 10.0
                    logger.warning(
                        "discovery.ratelimited",
                        extra={"url": self.url, "retry_after": wait, "attempt": attempt},
                    )
                    last_error = DiscoveryResponseError("Rate limit (429) fra søgningen")
                    time.sleep(wait)
                    continue
                if 400 <= status < 500:
                    raise DiscoveryResponseError(
                        f"HTTP {status} fra {self.url}. Kontrollér "
                        f"RETSINFORMATION_SEARCH_METHOD og _PARAMS. Sendt: {payload}"
                    )
                if status >= 500:
                    last_error = DiscoveryResponseError(f"HTTP {status} fra {self.url}")
                else:
                    return response

            if attempt < self.max_retries:
                backoff = min(2 ** (attempt - 1) * 2.0, 30.0)
                logger.warning(
                    "discovery.retry",
                    extra={"url": self.url, "attempt": attempt, "backoff": backoff},
                )
                time.sleep(backoff)

        raise last_error or DiscoveryResponseError(f"Kaldet til {self.url} mislykkedes")

    # -- Søgning ------------------------------------------------------------

    def search(self, query: DiscoveryQuery) -> DiscoveryResult:
        """Henter alle sider for én søgning.

        Stopper når en side er tom, når kildens eget resultattal er nået,
        eller når sideloftet er brugt. Leverer en side nøjagtig de samme
        numre som den forrige, betragtes pagineringen som virkningsløs, og
        der rejses :class:`DiscoveryPaginationError` frem for at løbe i
        ring.
        """
        result = DiscoveryResult(query=query)
        seen: set[str] = set()
        previous_page_ids: set[str] | None = None

        for index in range(self.max_pages):
            page = self.first_page + index
            offset = index * self.page_size

            decoded, _ = self.request_page(query, page=page, offset=offset)

            if result.reported_total is None:
                result.reported_total = find_reported_total(decoded)

            records = find_record_list(decoded)
            result.pages_fetched += 1

            if not records:
                break

            page_ids: set[str] = set()
            new_hits = 0
            for record in records:
                fields = extract_hit_fields(record)
                accession_number = fields["accession_number"]
                if not accession_number:
                    logger.warning(
                        "discovery.record.no_accession",
                        extra={"query": query.label, "keys": sorted(record)[:12]},
                    )
                    continue
                page_ids.add(accession_number)
                if accession_number in seen:
                    continue
                seen.add(accession_number)
                new_hits += 1
                result.hits.append(
                    DiscoveryHit(
                        **fields,
                        source_query=query.describe(),
                        raw=record,
                    )
                )

            logger.info(
                "discovery.page.fetched",
                extra={
                    "query": query.label,
                    "page": page,
                    "records": len(records),
                    "new": new_hits,
                    "total_so_far": len(result.hits),
                },
            )

            if previous_page_ids is not None and page_ids and page_ids == previous_page_ids:
                raise DiscoveryPaginationError(
                    f"Side {page} leverede de samme accessionsnumre som den forrige. "
                    "Sideparameteren har formentlig ikke effekt — kontrollér "
                    "RETSINFORMATION_SEARCH_PARAMS og RETSINFORMATION_SEARCH_PAGINATION."
                )
            previous_page_ids = page_ids

            if len(records) < self.page_size:
                break
            if result.reported_total is not None and len(seen) >= result.reported_total:
                break
        else:
            result.truncated = True
            logger.warning(
                "discovery.pages.exhausted",
                extra={"query": query.label, "max_pages": self.max_pages},
            )

        return result

    # -- Livscyklus ---------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "RetsinformationSearchClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _coerce_template(raw: dict[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    text = (raw or "").strip()
    if not text:
        raise DiscoveryConfigurationError(
            "RETSINFORMATION_SEARCH_PARAMS er tom. Angiv anmodningens parametre "
            'som JSON, f.eks. {"administrerendeMyndighed": "{authority}", '
            '"page": "{page}", "pageSize": "{page_size}"}'
        )
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise DiscoveryConfigurationError(
            f"RETSINFORMATION_SEARCH_PARAMS er ikke gyldig JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise DiscoveryConfigurationError(
            "RETSINFORMATION_SEARCH_PARAMS skal være et JSON-objekt."
        )
    return parsed


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None

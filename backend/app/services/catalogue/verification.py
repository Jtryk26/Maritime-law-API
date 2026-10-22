"""Explicit live checks of official HTML metadata, separate from ingestion.

No publication PDF or legal body is downloaded, indexed, cached or licensed by
this operation. The report contains URL/title observations and hashes only.
"""

import hashlib
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.models.international import LegalSource
from app.services.rights import RightsService


class MetadataText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, value):
        if not self.ignored:
            self.parts.append(value)


def _normalized(value):
    return " ".join(value.casefold().split())


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        source = LegalSource(key="imo", name="IMO", official_hosts=["www.imo.org"])
        RightsService.official_url(source, newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def fetch_official_metadata(url):
    source = LegalSource(key="imo", name="IMO", official_hosts=["www.imo.org"])
    RightsService.official_url(source, url)
    request = Request(url, headers={"User-Agent": "Maritime-law-API metadata verification/1.0"})
    # Default HTTPS handler retains the system's TLS/certificate verification.
    with build_opener(OfficialRedirects()).open(request, timeout=30) as response:
        RightsService.official_url(source, response.geturl())
        if response.status != 200 or response.headers.get_content_type() != "text/html":
            raise ValueError("Official source did not return an HTML metadata page with HTTP 200")
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Metadata page exceeds the bounded review size")
    parser = MetadataText()
    parser.feed(raw.decode("utf-8-sig", errors="replace"))
    return {"status": 200, "text": " ".join(parser.parts), "page_sha256": hashlib.sha256(raw).hexdigest(),
            "observed_at": datetime.now(timezone.utc).isoformat()}


def verify_sources(seed, *, fetch=fetch_official_metadata, pause=time.sleep, interval=1.0):
    """Read each cited page once. Changed HTML hashes do not invent legal changes."""
    urls = list(dict.fromkeys(url for record in seed.records
        for url in [record.canonical_url, *(proof.url for proof in record.verification_evidence)] if url))
    pages = {}
    errors = {}
    for index, url in enumerate(urls):
        try:
            source = LegalSource(key="imo", name="IMO", official_hosts=["www.imo.org"])
            RightsService.official_url(source, url)
            page = fetch(url)
            if page.get("status") != 200:
                raise ValueError("Metadata HTTP status is not 200")
            pages[url] = page
        except (OSError, ValueError) as exc:
            errors[url] = str(exc)
        if index + 1 < len(urls):
            pause(interval)
    results = []
    for record in seed.records:
        failures = []
        if record.review_state != "verified" or not record.verification_evidence:
            failures.append("Registry metadata is not source-verified")
        if record.canonical_url not in pages:
            failures.append("Canonical official metadata page unavailable")
        changed = []
        for proof in record.verification_evidence:
            page = pages.get(proof.url)
            if page is None:
                failures.append("Cited metadata page unavailable: " + proof.url)
            elif _normalized(proof.matched_title) not in _normalized(page["text"]):
                failures.append("Recorded official title no longer found: " + proof.url)
            elif page["page_sha256"] != proof.page_sha256:
                changed.append(proof.url)
        results.append({"external_id": record.external_id, "verified": not failures,
                        "official_url": record.canonical_url, "errors": failures,
                        "changed_page_hashes": changed})
    return {"provider": "imo", "checked_at": datetime.now(timezone.utc).isoformat(),
            "scope": "official HTML names and links only", "checked": len(results),
            "verified": sum(r["verified"] for r in results), "unique_pages": len(urls),
            "failed": sum(not r["verified"] for r in results), "page_errors": errors,
            "text_reuse_granted": False, "results": results}

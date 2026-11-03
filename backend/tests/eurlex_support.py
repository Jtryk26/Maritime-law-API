"""Invented Cellar responses and permission reviews; never real legal evidence."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs
import httpx
from app.services.eurlex.transport import CellarTransport

HTML = b'<html lang="en"><head><meta name="DC.identifier" content="32009L0016"/></head><body><div id="art_1"><h1>Invented maritime test act</h1><p>' + b'Article 1. Synthetic shipping safety text. ' * 12 + b'</p></div></body></html>'


def cellar_transport(requests=None, *, html=HTML, mutate=None):
    calls = requests if requests is not None else []
    def handler(request):
        calls.append(request)
        if request.url.path == "/webapi/rdf/sparql":
            query = parse_qs(request.url.query.decode())["query"][0]
            celex = query.split('STR(?celex) = "')[1].split('"')[0]
            payload = {"head": {"vars": ["work", "celex", "title", "date"]}, "results": {"bindings": [{
                "work": {"type": "uri", "value": f"http://publications.europa.eu/resource/celex/{celex}"},
                "celex": {"type": "literal", "value": celex},
                "title": {"type": "literal", "value": f"Invented maritime test act {celex}"},
                "date": {"type": "literal", "value": "2009-04-23"}}]}}
            if mutate:
                mutate(payload)
            return httpx.Response(200, json=payload, headers={"content-type": "application/sparql-results+json"})
        return httpx.Response(200, content=html, headers={"content-type": "application/xhtml+xml"})
    return CellarTransport(httpx.Client(transport=httpx.MockTransport(handler)))


def review_file(tmp_path, *, actions=None, version="32009L0016", unresolved=False):
    now = datetime.now(timezone.utc)
    value = [{"external_id": "32009L0016", "version_celex": version, "state": "open_reuse_verified",
        "allowed_actions": actions or ["fetch_text", "store_text", "display_text", "export_text", "index_text"],
        "evidence_url": "https://eur-lex.europa.eu/fixture/terms",
        "evidence_sha256": hashlib.sha256(b"Invented permission fixture").hexdigest(),
        "reviewer": "synthetic test reviewer", "reviewed_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(), "attribution": "Synthetic fixture, not EU law",
        "unresolved_conditions": unresolved}]
    path = tmp_path / "review.json"
    path.write_text(json.dumps(value))
    return path

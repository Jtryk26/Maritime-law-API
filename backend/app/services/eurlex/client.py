"""Curated SourceClient using Cellar's CDM SPARQL and content negotiation.

Metadata and body requests are separate. The adapter itself checks the existing
rights gate, in addition to ImportService's pre-fetch and persistence checks.
"""

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from urllib.parse import urlencode, urlsplit

from app.schemas.catalogue import MetadataVerification
from app.services.retsinformation.base import DocumentRef, NormalizedDocument, PermanentSourceError
from app.services.rights import Action, RightsDenied, RightsService

from .curated import Selection
from .transport import CellarTransport
from .xhtml import parse_xhtml

SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
CDM = "http://publications.europa.eu/ontology/cdm#"
LANGUAGES = {"en": "ENG", "da": "DAN"}


def legal_text(body: bytes, *, celex: str | None = None, language: str | None = None) -> str:
    parsed = parse_xhtml(body)
    if language and parsed.language != language:
        raise PermanentSourceError("Cellar manifestation language could not be verified")
    if celex and not any(celex == value or value.endswith("CELEX:" + celex) or value.endswith("/" + celex)
                         for value in parsed.identifiers):
        raise PermanentSourceError("Cellar manifestation CELEX identity could not be verified")
    return parsed.content


class CellarClient:
    kind = "eurlex"

    def __init__(self, records: list[Selection], rights: RightsService, *, transport: CellarTransport | None = None):
        self.records = {record.source_id: record for record in records}
        self.rights = rights
        self.transport = transport or CellarTransport()

    def _record(self, source_id):
        self.rights.require_source(self.kind)
        try:
            return self.records[source_id]
        except KeyError as exc:
            raise PermanentSourceError("CELEX expression is outside the curated selection") from exc

    def get_documents(self, *, explicit_ids=None):
        ids = list(explicit_ids) if explicit_ids is not None else list(self.records)
        for source_id in ids:
            record = self._record(source_id)
            yield DocumentRef(source_id=source_id, source_url=record.eli)

    def get_updated_documents(self, since):
        raise PermanentSourceError("M3 has no change feed; import explicit curated versions")

    def get_document_metadata(self, document_id):
        record = self._record(document_id)
        # CELEX/language inputs are strictly validated before SPARQL interpolation.
        query = f'''PREFIX cdm: <{CDM}>
SELECT DISTINCT ?work ?celex ?title ?date WHERE {{
  ?work cdm:resource_legal_id_celex ?celex .
  FILTER(STR(?celex) = "{record.version_celex}")
  ?expression cdm:expression_belongs_to_work ?work ;
    cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/{LANGUAGES[record.language]}> ;
    cdm:expression_title ?title .
  OPTIONAL {{ ?work cdm:work_date_document ?date }}
}} LIMIT 3'''
        url = SPARQL + "?" + urlencode({"query": query, "format": "application/sparql-results+json"})
        body, retrieved_url, media = self.transport.get(url, accept="application/sparql-results+json", language=record.language)
        if media not in {"application/sparql-results+json", "application/json"}:
            raise PermanentSourceError("Cellar metadata is not a SPARQL JSON result")
        try:
            rows = json.loads(body)["results"]["bindings"]
            if not 1 <= len(rows) <= 2:
                raise ValueError("missing or ambiguous expression")
            identities = {(r["work"]["value"], r["celex"]["value"], r["title"]["value"],
                           r.get("date", {}).get("value")) for r in rows}
            if len(identities) != 1:
                raise ValueError("ambiguous metadata")
            work, celex, title, adopted = identities.pop()
            if celex != record.version_celex or not title.strip() or len(title) > 1000:
                raise ValueError("identity/title mismatch")
            if not work.startswith(("http://publications.europa.eu/resource/", "https://publications.europa.eu/resource/")):
                raise ValueError("non-Cellar work")
            adopted_date = date.fromisoformat(adopted[:10]) if adopted else None
        except (KeyError, TypeError, ValueError) as exc:
            raise PermanentSourceError("Cellar metadata identity could not be verified") from exc
        now = datetime.now(timezone.utc)
        digest = hashlib.sha256(body).hexdigest()
        observation = MetadataVerification(url=retrieved_url, observed_at=now, status=200,
            page_sha256=digest, matched_title=title, match_kind="metadata_text")
        return NormalizedDocument(source=self.kind, source_id=document_id, title=title,
            document_type="EU regulation" if record.celex[5] == "R" else "EU directive",
            authority="European Union", source_url=f"https://eur-lex.europa.eu/legal-content/{record.language.upper()}/TXT/?uri=CELEX:{record.version_celex}",
            document_number=record.celex, rights_managed=True, retrieved_at=now,
            metadata={"language": record.language, "jurisdiction": "EU",
                "adopted_date": adopted_date.isoformat() if adopted_date and record.version_celex == record.celex else None,
                "identifiers": {"celex": record.celex, "eli": record.eli},
                "source_version": record.source_version,
                "source_hash": digest, "source_hash_scope": "cellar_metadata_response_bytes",
                "retrieval_url": retrieved_url, "cellar_work_uri": work,
                "evidence_links": [record.eli, retrieved_url],
                "metadata_review_state": "verified", "metadata_reviewer": "Cellar exact CELEX/language query",
                # Observation dates stay outside metadata hashes for idempotence.
                "verification_evidence": [observation.model_dump(mode="json", exclude={"observed_at"})],
                "acquisition_method": "cellar_sparql", "coverage_status": "metadata_only"})

    def get_document(self, document_id):
        record = self._record(document_id)
        evidence = self.rights.evidence(self.kind, document_id)
        if evidence is None:
            raise RightsDenied("No reviewed permissions exist for this CELEX expression")
        if ((evidence.restrictions or {}).get("version_celex") != record.version_celex
                or (evidence.restrictions or {}).get("language") != record.language):
            return self.get_document_metadata(document_id)
        if not all(self.rights.permits(evidence, action) for action in (Action.FETCH_TEXT, Action.STORE_TEXT)):
            raise RightsDenied("Reviewed fetch/store permissions are required before Cellar text acquisition")
        doc = self.get_document_metadata(document_id)
        url = f"https://publications.europa.eu/resource/celex/{record.version_celex}"
        body, final_url, media = self.transport.get(url, accept="application/xhtml+xml", language=record.language)
        if media not in {"application/xhtml+xml", "text/html"}:
            raise PermanentSourceError("Cellar did not return the requested HTML manifestation")
        parsed = parse_xhtml(body)
        work_path = urlsplit(doc.metadata["cellar_work_uri"]).path
        final_path = urlsplit(final_url).path
        same_work = work_path.startswith("/resource/cellar/") and (
            final_path == work_path or final_path.startswith(work_path + ".") or final_path.startswith(work_path + "/"))
        matching_identifier = any(record.version_celex == value or value.endswith("CELEX:" + record.version_celex)
                                  for value in parsed.identifiers)
        if not same_work and not matching_identifier:
            raise PermanentSourceError("Cellar manifestation does not belong to the verified CELEX work")
        if parsed.language != record.language:
            raise PermanentSourceError("Cellar manifestation language could not be verified")
        if not any(unit["kind"] == "article" for unit in parsed.structure["units"]):
            raise PermanentSourceError("Cellar XHTML has no recognized article structure")
        text = parsed.content
        # Recheck the grant after network I/O and before returning a body.
        if not all(self.rights.permits(evidence, action) for action in (Action.FETCH_TEXT, Action.STORE_TEXT)):
            raise RightsDenied("Rights review expired during acquisition")
        return replace(doc, content=text, metadata={**doc.metadata, "legal_structure": parsed.structure,
            "source_hash": hashlib.sha256(body).hexdigest(), "source_hash_scope": "cellar_html_response_bytes",
            "retrieval_url": final_url, "evidence_links": [*doc.metadata["evidence_links"], final_url],
            "acquisition_method": "cellar_content_negotiation"})

    def get_document_text(self, document_id):
        return self.get_document(document_id).content

    def close(self):
        self.transport.close()

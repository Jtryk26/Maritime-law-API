"""Geografisk analyse af dokumenter og vedligehold af områderne.

Pipelinen for ét dokument
=========================
1. **Spring over**, hvis titel og tekst er uændrede siden sidste analyse
   med samme detektorversion (``input_hash`` på `GeographicDetectionRun`).
2. **Tekst**: titlen og den normaliserede lovtekst
   (`normalize_legal_text` — samme tekst, strukturparseren og
   dokumentsiden bruger, så positioner og paragrafankre passer).
3. **Genkendelse** i gazetteeren (`gazetteer.py`).
4. **Klassifikation** af hvert fund (`classify.py`).
5. **Opløsning** til en række i `geographic_areas`. Stednavne uden område
   går i `geographic_review_queue` — der oprettes aldrig et område eller en
   polygon automatisk.
6. **Lagring**: automatiske, ikke-gennemgåede fund for dokumentet
   erstattes. En menneskelig afgørelse overføres aldrig automatisk: ændres
   noget, bevares den som historik, og det nye fund skal gennemgås igen.

Kaldes fra importen (se `ImportService._sync_geography`), fra
``POST /admin/geography/reanalyse/{id}`` og fra
``python -m app.cli geography analyse``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import Document, DocumentVersion
from app.models.geography import (
    APPROVED_REVIEW_STATUSES,
    Applicability,
    DocumentGeographicApplicability,
    EvidenceStatus,
    GeographicArea,
    GeographicDetectionRun,
    GeographicReviewQueueItem,
    GeometryKind,
    GeoReviewStatus,
)
from app.services.legal.structure import normalize_legal_text, parse_legal_structure
from app.services.rights import Action, RightsService

from .classify import classify_match, sentence_bounds
from .gazetteer import Gazetteer, GazetteerEntry, find_unknown_places, get_gazetteer

logger = get_logger(__name__)

__all__ = [
    "DETECTOR_VERSION",
    "RIGHTS_ACTIONS",
    "AnalysisResult",
    "GeographicAreasMissing",
    "StaleEvidence",
    "effective_geometry_kind",
    "official_geometry_problems",
    "evidence_hash",
    "PUBLICATION_LABELS",
    "is_published",
    "publication_status",
    "review_context_hash",
    "GeographyService",
    "geometry_bbox",
    "paragraph_anchor",
]

#: Hæves, når gazetteer-logikken eller klassifikationen ændres. En ny
#: version får alle dokumenter analyseret igen ved næste kørsel.
DETECTOR_VERSION = "geo-rules-2"

#: Højst så mange gemte fund pr. område pr. dokument. "Danmark" kan stå
#: hundrede gange i en lov; de stærkeste fund er nok til at vise sammenhængen.
MAX_MATCHES_PER_AREA = 5
_CONTEXT_MAX = 600

#: Det, analysen gør med teksten: henter og gemmer den, behandler den og
#: gemmer ordrette uddrag. Alle skal være tilladt (`RightsService.version_permits`).
RIGHTS_ACTIONS = (Action.FETCH_TEXT, Action.STORE_TEXT, Action.INDEX_TEXT, Action.SNIPPET)


class GeographicAreasMissing(RuntimeError):
    """Områderne er ikke seedet. Kun ``python -m app.cli geography seed`` opretter dem."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        shown = ", ".join(missing[:10]) + (f" (+{len(missing) - 10} flere)" if len(missing) > 10 else "")
        super().__init__(
            f"{len(missing)} geografiske områder fra config/geographic_areas.yaml findes ikke i "
            f"databasen: {shown}. Seed dem med ejerrollen: python -m app.cli geography seed"
        )


class StaleEvidence(LookupError):
    """Et fund, hvis bevis ikke længere passer med dokumentet eller området."""


@dataclass(slots=True)
class AnalysisResult:
    document_id: int
    status: str
    run_id: int | None = None
    matches: int = 0
    queued: int = 0
    by_area: dict[str, str] = field(default_factory=dict)


def _input_hash(
    title: str, content: str, status: str | None = None, gazetteer_fingerprint: str = ""
) -> str:
    """Titel, tekst, status og gazetteerens indhold.

    Status indgår, fordi en historisk status ændrer klassifikationen;
    gazetteeren, fordi et ændret område skal give en ny analyse.
    """
    digest = hashlib.sha256()
    for part in (title or "", content or "", status or "", gazetteer_fingerprint):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def evidence_hash(source_field: str, match_text: str, excerpt: str, status: str | None) -> str:
    """Fingeraftrykket af det, en gennemgang tager stilling til.

    Felt, fundet navn, det ordrette uddrag og dokumentets status. Ændres et
    af dem, gælder en tidligere gennemgang ikke længere for fundet.
    """
    digest = hashlib.sha256()
    for part in (source_field, match_text, excerpt, status or ""):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def review_context_hash(document: Document, version_id: int | None, area: GeographicArea) -> str:
    """Den juridiske kontekst, en menneskelig godkendelse gælder for.

    Dokumentversionen (dermed hele lovteksten — versioner er uforanderlige),
    titlen, den retlige status og områdets juridiske definition (type,
    tilknytning, hjemmel, datoer, udpegning). Ændres noget af det, gælder
    godkendelsen ikke længere — heller ikke selvom det fundne uddrag er
    uændret. Kan beregnes ved læsning uden at hente lovteksten.
    """
    designation = (area.metadata_json or {}).get("designation")
    digest = hashlib.sha256()
    for part in (
        str(version_id or ""),
        document.title or "",
        document.status or "",
        area.slug,
        area.area_type,
        area.parent_slug or "",
        area.legal_basis or "",
        str(area.effective_from or ""),
        str(area.effective_to or ""),
        json.dumps(designation, sort_keys=True, default=str),
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def excerpt_window(text: str, start: int, end: int, s_start: int, s_end: int) -> str:
    """Ordret uddrag: sætningen, eller et vindue om fundet, hvis sætningen er for lang."""
    if s_end - s_start <= _CONTEXT_MAX:
        return text[s_start:s_end].strip()
    half = max(0, (_CONTEXT_MAX - (end - start)) // 2)
    lo = max(s_start, start - half)
    hi = min(s_end, lo + _CONTEXT_MAX)
    return text[lo:hi].strip()


def _anchor_slug(value: str | None) -> str:
    """Spejler `anchorSlug` i frontend/src/lib/format.js."""
    return re.sub(r"[^0-9a-zæøå]+", "", str(value or "").lower().replace("§", ""))


def paragraph_anchor(structure, position: int) -> str | None:
    """Dokumentsidens anker for den paragraf (og det stykke), positionen ligger i."""
    for paragraph in structure.paragraphs:
        if paragraph.char_start <= position < paragraph.char_end:
            number = _anchor_slug(paragraph.paragraph_id)
            if not number:
                return None
            chapter = _anchor_slug(paragraph.chapter_no)
            anchor = f"p-{chapter}-{number}" if chapter else f"p-{number}"
            for sub in paragraph.subsections:
                if sub.char_start <= position < sub.char_end and sub.number > 1:
                    anchor += f"-stk{sub.number}"
                    break
            return anchor[:64]
    return None


def geometry_bbox(geometry: dict | None) -> tuple[float, float, float, float] | None:
    """(min_lon, min_lat, max_lon, max_lat) for en GeoJSON-geometri."""
    if not geometry:
        return None
    xs: list[float] = []
    ys: list[float] = []

    def walk(coords: Any) -> None:
        if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
            xs.append(float(coords[0]))
            ys.append(float(coords[1]))
        elif isinstance(coords, (list, tuple)):
            for item in coords:
                walk(item)

    if geometry.get("type") == "GeometryCollection":
        for part in geometry.get("geometries", []):
            walk(part.get("coordinates"))
    else:
        walk(geometry.get("coordinates"))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


class GeographyService:
    def __init__(self, session: Session, gazetteer: Gazetteer | None = None):
        self.session = session
        self.gazetteer = gazetteer or get_gazetteer()

    # -- Områder -------------------------------------------------------------

    def seed_areas(self) -> int:
        """Opretter eller opdaterer områderne fra gazetteeren. Idempotent.

        Metadata følger konfigurationen. Geometri overskrives kun, hvis
        området ikke allerede har en geometri fra en anden kilde
        (``approximate`` eller ``official``) — en indlæst officiel grænse
        må aldrig erstattes af et kortmærke.
        """
        existing = {a.slug: a for a in self.session.scalars(select(GeographicArea))}
        for entry in self.gazetteer.entries.values():
            area = existing.get(entry.slug)
            if area is None:
                area = GeographicArea(slug=entry.slug)
                self.session.add(area)
            self._apply_entry(area, entry)
        self.session.flush()
        return len(self.gazetteer.entries)

    @staticmethod
    def _apply_entry(area: GeographicArea, entry: GazetteerEntry) -> None:
        area.name = entry.name
        area.name_da = entry.name_da
        area.area_type = entry.area_type
        area.parent_slug = entry.parent
        area.legal_status = entry.legal_status
        area.legal_basis = entry.legal_basis
        area.source_url = entry.source_url
        area.source_reference = entry.source_reference
        area.effective_from = entry.effective_from
        area.effective_to = entry.effective_to
        area.aliases = list(entry.aliases)
        area.metadata_json = {**entry.metadata, "mention_only": entry.mention_only}
        if area.geometry_kind in (GeometryKind.APPROXIMATE.value, GeometryKind.OFFICIAL.value):
            return
        if entry.indicative_point:
            set_geometry(
                area,
                {"type": "Point", "coordinates": list(entry.indicative_point)},
                kind=GeometryKind.INDICATIVE_POINT,
                source="config/geographic_areas.yaml (omtrentligt kortmærke)",
            )
        else:
            set_geometry(area, None, kind=GeometryKind.NONE, source=None)

    def _areas_by_slug(self) -> dict[str, GeographicArea]:
        """Områderne, som de er seedet. Analysen opretter eller ændrer dem aldrig.

        Mangler et område fra gazetteeren, stopper analysen, før noget skrives:
        ellers ville fund for det område forsvinde uden spor.
        """
        areas = {a.slug: a for a in self.session.scalars(select(GeographicArea))}
        missing = sorted(set(self.gazetteer.entries) - set(areas))
        if missing:
            raise GeographicAreasMissing(missing)
        return areas

    def _rights_permit(self, document: Document, version: DocumentVersion | None) -> bool:
        """Den fælles rettighedspolitik (`RightsService`) for analysen.

        Analysen læser hele teksten og gemmer ordrette uddrag. Danske kilder
        uden registreret udbyder er tilladt som i resten af systemet; en
        registreret eller rettighedsstyret kilde kræver udtrykkelig tilladelse
        til hver af handlingerne.
        """
        rights = RightsService(self.session)
        if version is None:
            return rights.source(document.source) is None
        return all(rights.version_permits(version, action) for action in RIGHTS_ACTIONS)

    # -- Analyse -------------------------------------------------------------

    def analyse(
        self, document: Document, *, trigger: str = "import", force: bool = False
    ) -> AnalysisResult:
        """Analysér ét dokument.

        Returnerer status ``RIGHTS_DENIED`` uden at skrive noget, når
        rettighedspolitikken ikke tillader analysen. Rejser
        `GeographicAreasMissing`, før noget skrives, hvis områderne ikke er seedet.
        """
        version = (
            self.session.get(DocumentVersion, document.current_version_id)
            if document.current_version_id
            else None
        )
        if not self._rights_permit(document, version):
            logger.info("geography.rights_denied", extra={"document_id": document.id})
            return AnalysisResult(document.id, "RIGHTS_DENIED")
        areas = self._areas_by_slug()
        content = version.content if version is not None else ""
        input_hash = _input_hash(
            document.title, content, document.status, self.gazetteer.fingerprint
        )

        if not force:
            last = self.session.scalars(
                select(GeographicDetectionRun)
                .where(
                    GeographicDetectionRun.document_id == document.id,
                    GeographicDetectionRun.status == "COMPLETED",
                )
                .order_by(GeographicDetectionRun.id.desc())
                .limit(1)
            ).first()
            if last and last.input_hash == input_hash and last.detector_version == DETECTOR_VERSION:
                return AnalysisResult(document.id, "SKIPPED", run_id=last.id, matches=last.match_count)

        run = GeographicDetectionRun(
            document_id=document.id,
            document_version_id=version.id if version is not None else None,
            detector_version=DETECTOR_VERSION,
            input_hash=input_hash,
            trigger=trigger,
            status="COMPLETED",
            started_at=utcnow(),
        )
        self.session.add(run)
        self.session.flush()

        rows, unknown = self._detect(document, content, areas)

        # Fund, der venter på ny gennemgang, skal blive ved med at gøre det —
        # også efter endnu en ændring — og pege på den oprindelige afgørelse.
        pending: dict[int, int] = {}
        for waiting in self.session.scalars(
            select(DocumentGeographicApplicability).where(
                DocumentGeographicApplicability.document_id == document.id,
                DocumentGeographicApplicability.review_status == GeoReviewStatus.NEEDS_REREVIEW.value,
                DocumentGeographicApplicability.detection_method != "manual",
                DocumentGeographicApplicability.supersedes_id.is_not(None),
            ).order_by(DocumentGeographicApplicability.id)
        ):
            pending.setdefault(waiting.area_id, waiting.supersedes_id)

        # Automatiske fund, ingen har taget stilling til, erstattes.
        self.session.execute(
            delete(DocumentGeographicApplicability).where(
                DocumentGeographicApplicability.document_id == document.id,
                DocumentGeographicApplicability.review_status.in_(
                    [GeoReviewStatus.UNREVIEWED.value, GeoReviewStatus.NEEDS_REREVIEW.value]
                ),
                DocumentGeographicApplicability.detection_method != "manual",
            )
        )
        by_id = {a.id: a for a in areas.values()}
        for row in rows:
            row.document_version_id = run.document_version_id
            row.detection_run_id = run.id
            row.context_hash = review_context_hash(document, run.document_version_id, by_id[row.area_id])
        rows = self._reconcile_reviews(document, run, rows, by_id, pending)

        result = AnalysisResult(document.id, "COMPLETED", run_id=run.id)
        slugs = {a.id: s for s, a in areas.items()}
        for row in rows:
            self.session.add(row)
            result.matches += 1
            result.by_area.setdefault(slugs[row.area_id], row.applicability)

        result.queued = self._queue_unknown(document, run.document_version_id, unknown)
        run.match_count = result.matches
        run.queued_count = result.queued
        run.finished_at = datetime.now(timezone.utc)
        self.session.flush()
        logger.info(
            "geography.analysed",
            extra={"document_id": document.id, "matches": result.matches, "queued": result.queued},
        )
        return result

    def _reconcile_reviews(
        self,
        document: Document,
        run: GeographicDetectionRun,
        rows: list[DocumentGeographicApplicability],
        areas: dict[int, GeographicArea],
        pending: dict[int, int] | None = None,
    ) -> list[DocumentGeographicApplicability]:
        """En menneskelig afgørelse overføres aldrig automatisk.

        For hver gældende afgørelse (bekræftet, rettet, afvist eller manuel):

        * **Intet er ændret** — samme version, titel, status og områdedefinition
          (`context_hash`) og samme fund: afgørelsen står. Det sker kun ved en
          tvungen genkørsel.
        * **Noget er ændret** — tekst, titel, status, version eller områdets
          juridiske definition: den gamle række bliver historik (`superseded`)
          med alle gennemgangsfelter urørte. Nye fund i området oprettes som
          `needs_rereview` med `supersedes_id`. Det gælder også, når uddraget
          er uændret: konteksten kan være ændret et andet sted i teksten.
          Den tidligere afgørelse vises kun som kontekst (se API'et).

        Et tidligere afvist fund bliver altså `needs_rereview` — ikke
        publiceret, men heller ikke stadig afvist.

        `pending` er områder, hvor et fund allerede ventede på ny gennemgang
        (område -> den oprindelige afgørelse). Ændres dokumentet igen, før
        nogen har set på det, venter de nye fund stadig og peger fortsat på
        den oprindelige afgørelse.
        """
        pending = pending or {}
        prior = self.session.scalars(
            select(DocumentGeographicApplicability).where(
                DocumentGeographicApplicability.document_id == document.id,
                DocumentGeographicApplicability.evidence_status == EvidenceStatus.CURRENT.value,
            ).order_by(DocumentGeographicApplicability.id)
        ).all()
        unused = list(rows)
        out: list[DocumentGeographicApplicability] = []
        stale_by_area: dict[int, int] = {}

        for old in prior:
            area = areas.get(old.area_id)
            context = review_context_hash(document, run.document_version_id, area) if area else None
            unchanged = context is not None and old.context_hash == context

            if old.detection_method == "manual":
                if unchanged:
                    continue
                old.evidence_status = EvidenceStatus.SUPERSEDED.value
                origin = (
                    old.supersedes_id
                    if old.review_status == GeoReviewStatus.NEEDS_REREVIEW.value and old.supersedes_id
                    else old.id
                )
                out.append(_manual_rereview_copy(old, run, context, origin))
                continue

            twin = next(
                (
                    r for r in unused
                    if r.area_id == old.area_id
                    and r.evidence_hash == old.evidence_hash
                    and (r.source_field, r.char_start, r.char_end)
                    == (old.source_field, old.char_start, old.char_end)
                ),
                None,
            )
            if twin is not None and unchanged and old.document_version_id == run.document_version_id:
                unused.remove(twin)
                continue  # Tvungen genkørsel uden ændringer: afgørelsen står.

            old.evidence_status = EvidenceStatus.SUPERSEDED.value
            stale_by_area.setdefault(old.area_id, old.id)
            if twin is not None:
                unused.remove(twin)
                twin.review_status = GeoReviewStatus.NEEDS_REREVIEW.value
                twin.supersedes_id = old.id
                out.append(twin)

        for row in unused:
            origin = stale_by_area.get(row.area_id) or pending.get(row.area_id)
            if origin:
                row.review_status = GeoReviewStatus.NEEDS_REREVIEW.value
                row.supersedes_id = origin
            out.append(row)
        self.session.flush()
        return out

    def _detect(self, document: Document, content: str, areas: dict[str, GeographicArea]):
        normalized = normalize_legal_text(content)
        structure = parse_legal_structure(content, document_title=document.title) if normalized else None
        candidates: list[DocumentGeographicApplicability] = []
        content_matches = []

        for source_field, text in (("title", document.title or ""), ("content", normalized)):
            matches = self.gazetteer.find(text)
            if source_field == "content":
                content_matches = matches
            for match in matches:
                area = areas.get(match.slug)
                entry = self.gazetteer.entries.get(match.slug)
                if area is None or entry is None:
                    continue
                verdict = classify_match(
                    text,
                    match.start,
                    match.end,
                    source_field=source_field,
                    document_status=document.status,
                    mention_only=entry.mention_only,
                    weight=entry.weight,
                    quality=match.quality,
                )
                s_start, s_end = sentence_bounds(text, match.start, match.end)
                excerpt = excerpt_window(text, match.start, match.end, s_start, s_end)
                candidates.append(
                    DocumentGeographicApplicability(
                        document_id=document.id,
                        area_id=area.id,
                        source_field=source_field,
                        match_text=match.text[:512],
                        char_start=match.start,
                        char_end=match.end,
                        paragraph_anchor=(
                            paragraph_anchor(structure, match.start)
                            if source_field == "content" and structure
                            else None
                        ),
                        context=excerpt,
                        evidence_hash=evidence_hash(
                            source_field, match.text, excerpt, document.status
                        ),
                        applicability=verdict.applicability.value,
                        confidence=verdict.confidence,
                        cue=verdict.cue,
                        detection_method=match.method,
                        detector_version=DETECTOR_VERSION,
                        review_status=GeoReviewStatus.UNREVIEWED.value,
                    )
                )

        unknown = find_unknown_places(normalized, self.gazetteer, content_matches)
        return _cap_per_area(candidates), [(u, normalized) for u in unknown]

    def _queue_unknown(self, document: Document, version_id: int | None, unknown) -> int:
        """Ukendte stednavne i køen. Et afvist eller løst navn genåbnes ikke."""
        self.session.execute(
            delete(GeographicReviewQueueItem).where(
                GeographicReviewQueueItem.document_id == document.id,
                GeographicReviewQueueItem.status == "open",
            )
        )
        closed = set(
            self.session.scalars(
                select(GeographicReviewQueueItem.candidate_key).where(
                    GeographicReviewQueueItem.document_id == document.id
                )
            )
        )
        count = 0
        for candidate, text in unknown:
            if candidate.key in closed:
                continue
            s_start, s_end = sentence_bounds(text, candidate.start, candidate.end)
            self.session.add(
                GeographicReviewQueueItem(
                    document_id=document.id,
                    document_version_id=version_id,
                    reason="unknown_area",
                    candidate_key=candidate.key,
                    match_text=candidate.text[:512],
                    char_start=candidate.start,
                    char_end=candidate.end,
                    context=text[s_start:s_end].strip()[:_CONTEXT_MAX],
                )
            )
            count += 1
        return count

    # -- Gennemgang ----------------------------------------------------------

    def review(
        self,
        applicability_id: int,
        *,
        status: GeoReviewStatus,
        reviewer: str,
        note: str | None = None,
        applicability: Applicability | None = None,
    ) -> DocumentGeographicApplicability:
        """Et menneskes afgørelse af ét fund. Rettes klassifikationen, bliver status `corrected`."""
        row = self.session.get(DocumentGeographicApplicability, applicability_id)
        if row is None:
            raise LookupError(f"Geografisk fund {applicability_id} findes ikke.")
        if row.evidence_status != EvidenceStatus.CURRENT.value:
            raise LookupError(
                f"Geografisk fund {applicability_id} hører til en tidligere tekst og er historik. "
                "Gennemgå det aktuelle fund i stedet."
            )
        if status not in (GeoReviewStatus.CONFIRMED, GeoReviewStatus.REJECTED):
            raise ValueError("En gennemgang er 'confirmed' eller 'rejected'.")
        document = self.session.get(Document, row.document_id)
        area = self.session.get(GeographicArea, row.area_id)
        if (
            document is None
            or area is None
            or row.document_version_id is None
            or row.document_version_id != document.current_version_id
            or row.context_hash != review_context_hash(document, document.current_version_id, area)
        ):
            # Beviset passer ikke længere med dokumentets version, titel, status
            # eller områdets definition. Rækken røres ikke; tidligere
            # gennemgange står uændret som historik.
            raise StaleEvidence(
                f"Geografisk fund {applicability_id} bygger på en ældre version, titel, status "
                f"eller områdedefinition end dokumentets nuværende. Analysér dokumentet igen "
                f"(python -m app.cli geography analyse --document-id {row.document_id} --force) "
                "og gennemgå det nye fund."
            )
        if applicability is not None and applicability.value != row.applicability:
            row.applicability = applicability.value
            status = GeoReviewStatus.CORRECTED
        row.review_status = status.value
        row.reviewed_by = reviewer
        row.reviewed_at = utcnow()
        row.review_note = note
        self.session.flush()
        return row

    def add_manual(
        self,
        document: Document,
        area: GeographicArea,
        *,
        applicability: Applicability,
        reviewer: str,
        note: str | None = None,
    ) -> DocumentGeographicApplicability:
        """Et manuelt fund — fx når anvendelsen følger af hjemmel, ikke af ordlyden."""
        version = (
            self.session.get(DocumentVersion, document.current_version_id)
            if document.current_version_id
            else None
        )
        row = DocumentGeographicApplicability(
            document_id=document.id,
            document_version_id=version.id if version is not None else None,
            context_hash=review_context_hash(document, version.id if version is not None else None, area),
            area_id=area.id,
            source_field="manual",
            match_text=area.name,
            applicability=applicability.value,
            confidence=1.0,
            cue="manual",
            detection_method="manual",
            detector_version="manual",
            review_status=GeoReviewStatus.CONFIRMED.value,
            reviewed_by=reviewer,
            reviewed_at=utcnow(),
            review_note=note,
        )
        self.session.add(row)
        self.session.flush()
        return row


def _https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def official_geometry_problems(area: GeographicArea, source: str | None) -> list[str]:
    """Hvorfor en geometri ikke må kaldes en officiel grænse. Tom liste: den må.

    Kræver alt dette, ellers er geometrien højst ``approximate``:

    * området er efterprøvet (``legal_status = verified``);
    * ``metadata.boundary_evidence`` i config/geographic_areas.yaml med
      ``source_url`` (HTTPS), ``reviewer``, ``reviewed_at`` (ikke i fremtiden),
      ``licence`` og ``reuse_permitted: true``;
    * geometriens kilde er netop den efterprøvede ``source_url``.
    """
    problems: list[str] = []
    if area.legal_status != "verified":
        problems.append(f"området er ikke efterprøvet (legal_status = {area.legal_status!r})")
    evidence = (area.metadata_json or {}).get("boundary_evidence")
    if not isinstance(evidence, dict):
        return problems + ["metadata.boundary_evidence mangler"]
    if not _https_url(evidence.get("source_url")):
        problems.append("boundary_evidence.source_url skal være en HTTPS-adresse")
    if not str(evidence.get("reviewer") or "").strip():
        problems.append("boundary_evidence.reviewer mangler")
    try:
        reviewed = date.fromisoformat(str(evidence.get("reviewed_at")))
    except ValueError:
        problems.append("boundary_evidence.reviewed_at skal være en dato (ÅÅÅÅ-MM-DD)")
    else:
        if reviewed > date.today():
            problems.append("boundary_evidence.reviewed_at ligger i fremtiden")
    if not str(evidence.get("licence") or "").strip():
        problems.append("boundary_evidence.licence mangler")
    if evidence.get("reuse_permitted") is not True:
        problems.append("boundary_evidence.reuse_permitted skal være true")
    if not source or source != evidence.get("source_url"):
        problems.append("geometriens kilde skal være boundary_evidence.source_url")
    return problems


def effective_geometry_kind(area: GeographicArea) -> str:
    """Den art, geometrien må vises som. En officiel grænse uden gyldigt grundlag vises som omtrentlig."""
    if area.geometry_kind == GeometryKind.OFFICIAL.value and official_geometry_problems(
        area, area.geometry_source
    ):
        return GeometryKind.APPROXIMATE.value
    return area.geometry_kind


def set_geometry(
    area: GeographicArea, geometry: dict | None, *, kind: GeometryKind, source: str | None
) -> None:
    """Sætter geometri, art, kilde og den afledte bounding box samlet.

    ``official`` kræver efterprøvet grundlag (`official_geometry_problems`);
    ellers rejses `ValueError`, og området røres ikke.
    """
    if kind == GeometryKind.OFFICIAL:
        problems = official_geometry_problems(area, source)
        if problems:
            raise ValueError(
                f"'{area.slug}' kan ikke få en officiel grænse: " + "; ".join(problems)
                + ". Brug --kind approximate."
            )
    area.geometry = geometry
    area.geometry_kind = kind.value
    area.geometry_source = source
    bbox = geometry_bbox(geometry)
    area.min_lon, area.min_lat, area.max_lon, area.max_lat = bbox if bbox else (None,) * 4


def _cap_per_area(rows: list[DocumentGeographicApplicability]) -> list[DocumentGeographicApplicability]:
    """Højst `MAX_MATCHES_PER_AREA` pr. område og klassifikation — de stærkeste."""
    groups: dict[tuple[int, str], list[DocumentGeographicApplicability]] = defaultdict(list)
    for row in rows:
        groups[(row.area_id, row.applicability)].append(row)
    kept: list[DocumentGeographicApplicability] = []
    for group in groups.values():
        title_first = sorted(
            group, key=lambda r: (r.source_field != "title", -r.confidence, r.char_start or 0)
        )
        kept.extend(title_first[:MAX_MATCHES_PER_AREA])
    return sorted(kept, key=lambda r: (r.source_field != "title", r.char_start or 0))


def _manual_rereview_copy(
    old: DocumentGeographicApplicability,
    run: GeographicDetectionRun,
    context: str | None,
    origin: int,
) -> DocumentGeographicApplicability:
    """Et manuelt fund på et ændret dokument: samme påstand, men ikke godkendt."""
    return DocumentGeographicApplicability(
        document_id=old.document_id,
        document_version_id=run.document_version_id,
        detection_run_id=run.id,
        area_id=old.area_id,
        source_field=old.source_field,
        match_text=old.match_text,
        applicability=old.applicability,
        confidence=old.confidence,
        cue=old.cue,
        detection_method="manual",
        detector_version=old.detector_version,
        review_status=GeoReviewStatus.NEEDS_REREVIEW.value,
        review_note=f"Dokumentet er ændret siden manuel tilknytning #{origin}.",
        context_hash=context,
        supersedes_id=origin,
    )


# -- Publicering ---------------------------------------------------------------

#: Etiketter, som API og kort viser ordret. Kun en udtrykkelig menneskelig
#: godkendelse af det aktuelle bevis giver "Reviewed".
PUBLICATION_LABELS = {
    "reviewed": "Reviewed geographic applicability",
    "reviewed_mention": "Reviewed geographic mention",
    "detected_unreviewed": "Detected geographic applicability — not reviewed",
    "detected_mention_unreviewed": "Detected geographic mention — not reviewed",
    "needs_rereview": (
        "Detected geographic applicability — not reviewed "
        "(an earlier review no longer matches the current text)"
    ),
    "superseded": "Historical review of an earlier text — not applied to the current text",
}


def publication_status(
    row: DocumentGeographicApplicability, document: Document, area: GeographicArea | None
) -> str:
    """Hvordan et fund må præsenteres. Confidence og klassifikation alene er aldrig godkendelse.

    En godkendelse tæller kun, når rækken er gældende (`evidence_status`),
    hører til dokumentets aktuelle version, OG dens `context_hash` passer med
    dokumentets nuværende titel, status og områdets nuværende definition.
    Er noget ændret, uden at analysen har kørt (fx fordi funktionen var slået
    fra under en import), vises den som "needs re-review" — ikke som godkendt.
    """
    if row.evidence_status != EvidenceStatus.CURRENT.value:
        return "superseded"
    mention = row.applicability == Applicability.MENTIONS.value
    if row.review_status in APPROVED_REVIEW_STATUSES:
        if (
            area is not None
            and row.document_version_id is not None
            and row.document_version_id == document.current_version_id
            and row.context_hash == review_context_hash(document, document.current_version_id, area)
        ):
            return "reviewed_mention" if mention else "reviewed"
        return "needs_rereview"
    if row.review_status == GeoReviewStatus.NEEDS_REREVIEW.value:
        return "needs_rereview"
    return "detected_mention_unreviewed" if mention else "detected_unreviewed"


def is_published(row: DocumentGeographicApplicability, document: Document, area: GeographicArea | None) -> bool:
    """Må fundet vises som gennemgået anvendelighed?"""
    return publication_status(row, document, area) in ("reviewed", "reviewed_mention")

"""Persistering, versionering og vedligeholdelse af søgeindeks.

Al skrivning til dokumenttabellerne går gennem dette lag. Importeren
orkestrerer; repositoriet ved hvordan data gemmes.

Versioneringsregler
===================
* Nyt dokument            -> version 1, ændringstype CREATED.
* Uændret indholdshash    -> INGEN ny version.
* Ændret indholdshash     -> ny version med næste nummer, gammel version
                             bevares, `current_version_id` opdateres,
                             ændringstype CONTENT_UPDATED.
* Kun ændret metadata     -> ingen ny version, ændringstype
                             METADATA_UPDATED (eller STATUS_CHANGED, hvis
                             det er statusfeltet der ændrede sig).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.text import content_hash, fold, normalize_whitespace
from app.models import (
    Category,
    ChangeLogEntry,
    ChangeType,
    Document,
    DocumentCategory,
    DocumentVersion,
)
from app.services.categorization.base import CategorizationResult, CategoryDefinition
from app.services.relevance.base import RelevanceResult
from app.services.retsinformation.base import NormalizedDocument

logger = get_logger(__name__)

__all__ = ["DocumentRepository", "StoreOutcome"]

#: Metadatafelter der indgår i metadata-hashen.
_METADATA_FIELDS = (
    "title",
    "short_title",
    "document_type",
    "authority",
    "published_date",
    "effective_date",
    "status",
    "source_url",
    "document_number",
)


@dataclass(slots=True)
class StoreOutcome:
    """Resultatet af at gemme ét dokument."""

    document: Document
    created: bool
    content_changed: bool
    metadata_changed: bool
    version_number: int
    change_types: list[ChangeType]

    @property
    def unchanged(self) -> bool:
        return not (self.created or self.content_changed or self.metadata_changed)


def _metadata_hash(doc: NormalizedDocument) -> str:
    """Deterministisk hash over de normaliserede metadatafelter."""
    parts: list[str] = []
    for field_name in _METADATA_FIELDS:
        value = getattr(doc, field_name, None)
        parts.append(f"{field_name}={value if value is not None else ''}")
    return content_hash("|".join(parts))


class DocumentRepository:
    """Læse- og skriveoperationer for dokumenter."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._dialect = session.get_bind().dialect.name

    # -- Kategorier ---------------------------------------------------------

    def sync_categories(self, definitions: list[CategoryDefinition]) -> dict[str, Category]:
        """Opretter eller opdaterer kategorierne fra konfigurationen.

        Idempotent. Kategorier fjernes ikke automatisk, da eksisterende
        dokumenter kan referere til dem.
        """
        existing = {c.slug: c for c in self.session.scalars(select(Category)).all()}

        for definition in definitions:
            category = existing.get(definition.slug)
            if category is None:
                category = Category(
                    slug=definition.slug,
                    name=definition.name,
                    description=definition.description,
                    sort_order=definition.sort_order,
                )
                self.session.add(category)
                existing[definition.slug] = category
            else:
                category.name = definition.name
                category.description = definition.description
                category.sort_order = definition.sort_order

        self.session.flush()
        return existing

    def _category_map(self) -> dict[str, Category]:
        return {c.slug: c for c in self.session.scalars(select(Category)).all()}

    # -- Opslag -------------------------------------------------------------

    def find_by_source(self, source: str, source_id: str) -> Document | None:
        return self.session.scalars(
            select(Document).where(
                Document.source == source,
                Document.source_id == source_id,
            )
        ).first()

    # -- Skrivning ----------------------------------------------------------

    def store(
        self,
        normalized: NormalizedDocument,
        relevance: RelevanceResult,
        categorization: CategorizationResult,
        *,
        import_run_id: int | None = None,
    ) -> StoreOutcome:
        """Opretter eller opdaterer et dokument og dets version.

        Kalderen styrer transaktionen; her flushes kun.
        """
        existing = self.find_by_source(normalized.source, normalized.source_id)
        new_content_hash = content_hash(normalized.content or "")
        new_metadata_hash = _metadata_hash(normalized)
        retrieved_at = normalized.retrieved_at or datetime.now(timezone.utc)

        if existing is None:
            return self._create(
                normalized,
                relevance,
                categorization,
                content_hash_value=new_content_hash,
                metadata_hash_value=new_metadata_hash,
                retrieved_at=retrieved_at,
                import_run_id=import_run_id,
            )

        return self._update(
            existing,
            normalized,
            relevance,
            categorization,
            content_hash_value=new_content_hash,
            metadata_hash_value=new_metadata_hash,
            retrieved_at=retrieved_at,
            import_run_id=import_run_id,
        )

    def _create(
        self,
        normalized: NormalizedDocument,
        relevance: RelevanceResult,
        categorization: CategorizationResult,
        *,
        content_hash_value: str,
        metadata_hash_value: str,
        retrieved_at: datetime,
        import_run_id: int | None,
    ) -> StoreOutcome:
        document = Document(
            source=normalized.source,
            source_id=normalized.source_id,
            source_url=normalized.source_url,
            retsinformation_id=normalized.retsinformation_id,
            document_number=normalized.document_number,
            is_synthetic=normalized.is_synthetic,
            title=normalize_whitespace(normalized.title),
            short_title=normalized.short_title,
            document_type=normalized.document_type,
            authority=normalized.authority,
            published_date=normalized.published_date,
            effective_date=normalized.effective_date,
            status=normalized.status,
            is_maritime=relevance.is_maritime,
            maritime_score=relevance.score,
            relevance_details=relevance.to_json(),
            relevance_engine=relevance.engine,
            last_retrieved_at=retrieved_at,
        )
        self.session.add(document)
        self.session.flush()  # tildeler document.id

        version = self._add_version(
            document,
            normalized,
            version_number=1,
            content_hash_value=content_hash_value,
            metadata_hash_value=metadata_hash_value,
            retrieved_at=retrieved_at,
        )
        document.current_version_id = version.id
        # Klassifikationen gælder netop denne version af teksten.
        document.relevance_version_id = version.id

        self._apply_categories(document, categorization)
        self._refresh_search_index(document, normalized.content or "")

        self._log_change(
            document,
            ChangeType.CREATED,
            new_version_id=version.id,
            import_run_id=import_run_id,
            detail=f"Dokument oprettet med maritim score {relevance.score}",
        )
        self.session.flush()

        logger.info(
            "import.document.created",
            extra={
                "source_id": document.source_id,
                "version": 1,
                "score": relevance.score,
                "maritime": relevance.is_maritime,
                "categories": len(categorization.assignments),
            },
        )
        return StoreOutcome(
            document=document,
            created=True,
            content_changed=True,
            metadata_changed=True,
            version_number=1,
            change_types=[ChangeType.CREATED],
        )

    def _update(
        self,
        document: Document,
        normalized: NormalizedDocument,
        relevance: RelevanceResult,
        categorization: CategorizationResult,
        *,
        content_hash_value: str,
        metadata_hash_value: str,
        retrieved_at: datetime,
        import_run_id: int | None,
    ) -> StoreOutcome:
        current = self._current_version(document)
        old_version_id = current.id if current else None

        content_changed = current is None or current.content_hash != content_hash_value
        status_changed = document.status != normalized.status
        metadata_changed = (
            current is None
            or current.metadata_hash != metadata_hash_value
        )

        change_types: list[ChangeType] = []
        version_number = current.version_number if current else 0

        if content_changed:
            version_number = self._next_version_number(document.id)
            version = self._add_version(
                document,
                normalized,
                version_number=version_number,
                content_hash_value=content_hash_value,
                metadata_hash_value=metadata_hash_value,
                retrieved_at=retrieved_at,
            )
            # Tidligere version bevares uændret; kun peger flyttes.
            document.current_version_id = version.id
            change_types.append(ChangeType.CONTENT_UPDATED)
            self._log_change(
                document,
                ChangeType.CONTENT_UPDATED,
                old_version_id=old_version_id,
                new_version_id=version.id,
                import_run_id=import_run_id,
                detail=f"Indhold ændret, version {version_number} oprettet",
            )
        elif current is not None and current.metadata_hash != metadata_hash_value:
            # Metadata ændret uden indholdsændring: opdatér den aktuelle
            # versions metadata frem for at oprette en overflødig version.
            current.metadata_hash = metadata_hash_value
            current.metadata_json = self._version_metadata(normalized)

        if status_changed:
            change_types.append(ChangeType.STATUS_CHANGED)
            self._log_change(
                document,
                ChangeType.STATUS_CHANGED,
                old_version_id=old_version_id,
                new_version_id=document.current_version_id,
                import_run_id=import_run_id,
                detail=f"Status ændret fra {document.status!r} til {normalized.status!r}",
            )
        elif metadata_changed and not content_changed:
            change_types.append(ChangeType.METADATA_UPDATED)
            self._log_change(
                document,
                ChangeType.METADATA_UPDATED,
                old_version_id=old_version_id,
                new_version_id=document.current_version_id,
                import_run_id=import_run_id,
                detail="Metadata ændret uden indholdsændring",
            )

        # Normaliseret metadata opdateres altid til seneste kendte værdi.
        document.source_url = normalized.source_url or document.source_url
        document.retsinformation_id = (
            normalized.retsinformation_id or document.retsinformation_id
        )
        document.title = normalize_whitespace(normalized.title) or document.title
        document.short_title = normalized.short_title
        document.document_type = normalized.document_type
        document.authority = normalized.authority
        document.published_date = normalized.published_date
        document.effective_date = normalized.effective_date
        document.status = normalized.status
        document.document_number = normalized.document_number or document.document_number
        document.is_synthetic = normalized.is_synthetic
        document.is_maritime = relevance.is_maritime
        document.maritime_score = relevance.score
        document.relevance_details = relevance.to_json()
        document.relevance_engine = relevance.engine
        # Vurderingen er netop foretaget på den nu aktuelle version.
        document.relevance_version_id = document.current_version_id
        document.last_retrieved_at = retrieved_at

        self._apply_categories(document, categorization)
        self._refresh_search_index(document, normalized.content or "")
        self.session.flush()

        if change_types:
            logger.info(
                "import.document.updated",
                extra={
                    "source_id": document.source_id,
                    "version": version_number,
                    "changes": ",".join(c.value for c in change_types),
                    "score": relevance.score,
                },
            )
        else:
            logger.debug(
                "import.document.unchanged",
                extra={"source_id": document.source_id, "version": version_number},
            )

        return StoreOutcome(
            document=document,
            created=False,
            content_changed=content_changed,
            metadata_changed=metadata_changed or status_changed,
            version_number=version_number,
            change_types=change_types,
        )

    # -- Versioner ----------------------------------------------------------

    def _current_version(self, document: Document) -> DocumentVersion | None:
        if document.current_version_id is None:
            return None
        return self.session.get(DocumentVersion, document.current_version_id)

    def _next_version_number(self, document_id: int) -> int:
        highest = self.session.scalar(
            select(func.max(DocumentVersion.version_number)).where(
                DocumentVersion.document_id == document_id
            )
        )
        return (highest or 0) + 1

    def _version_metadata(self, normalized: NormalizedDocument) -> dict[str, Any]:
        """Metadata gemt sammen med versionen.

        Normaliserede og rå felter holdes adskilt, så det altid kan
        efterprøves hvad kilden faktisk leverede.
        """
        return {
            "normalized": {
                "title": normalized.title,
                "short_title": normalized.short_title,
                "document_type": normalized.document_type,
                "authority": normalized.authority,
                "published_date": (
                    normalized.published_date.isoformat() if normalized.published_date else None
                ),
                "effective_date": (
                    normalized.effective_date.isoformat() if normalized.effective_date else None
                ),
                "status": normalized.status,
                "source_url": normalized.source_url,
                "extra": normalized.metadata,
            },
            "source": normalized.raw_metadata,
        }

    def _add_version(
        self,
        document: Document,
        normalized: NormalizedDocument,
        *,
        version_number: int,
        content_hash_value: str,
        metadata_hash_value: str,
        retrieved_at: datetime,
    ) -> DocumentVersion:
        version = DocumentVersion(
            document_id=document.id,
            version_number=version_number,
            content=normalized.content or "",
            content_hash=content_hash_value,
            metadata_hash=metadata_hash_value,
            metadata_json=self._version_metadata(normalized),
            retrieved_at=retrieved_at,
        )
        self.session.add(version)
        self.session.flush()  # tildeler version.id
        return version

    # -- Kategorier pr. dokument -------------------------------------------

    def _apply_categories(
        self, document: Document, categorization: CategorizationResult
    ) -> None:
        """Sætter dokumentets kategorier til resultatet af kategoriseringen."""
        category_map = self._category_map()
        desired = {
            a.slug: a
            for a in categorization.assignments
            if a.slug in category_map
        }

        missing = {a.slug for a in categorization.assignments} - set(category_map)
        if missing:
            logger.warning(
                "categorization.unknown_slug",
                extra={"source_id": document.source_id, "slugs": ",".join(sorted(missing))},
            )

        existing_links = {link.category.slug: link for link in document.category_links}

        # Fjern kategorier der ikke længere er tildelt.
        for slug, link in list(existing_links.items()):
            if slug not in desired:
                document.category_links.remove(link)

        # Tilføj eller opdatér.
        for slug, assignment in desired.items():
            link = existing_links.get(slug)
            if link is None:
                document.category_links.append(
                    DocumentCategory(
                        category_id=category_map[slug].id,
                        confidence=assignment.confidence,
                        matched_terms=assignment.matched_terms,
                    )
                )
            else:
                link.confidence = assignment.confidence
                link.matched_terms = assignment.matched_terms

        self.session.flush()

    # -- Søgeindeks ---------------------------------------------------------

    def _refresh_search_index(self, document: Document, content: str) -> None:
        """Genopbygger dokumentets søgetekst og tsvector.

        Kaldes ved enhver ændring af titel, indhold, myndighed eller
        kategorier, så indekset aldrig kommer ud af trit med data.
        """
        category_names = " ".join(
            link.category.name for link in document.category_links if link.category
        )

        title_parts = [
            document.title or "",
            document.short_title or "",
            document.document_number or "",
        ]
        # Foldes ved indeksering, så matchning er uafhængig af danske tegn.
        # Søgetermer foldes tilsvarende i tokenize().
        document.search_title = fold(normalize_whitespace(" ".join(filter(None, title_parts))))
        document.search_text = fold(
            normalize_whitespace(
                " ".join(
                    filter(
                        None,
                        [
                            *title_parts,
                            document.document_type or "",
                            document.authority or "",
                            category_names,
                            content,
                        ],
                    )
                )
            )
        )

        if self._dialect != "postgresql":
            # SQLite og øvrige bruger search_text direkte.
            return

        self.session.flush()
        # Vægtet tsvector: A=titel, B=myndighed/type, C=kategorier, D=brødtekst.
        # ts_rank prioriterer dermed titelmatch over tekstmatch.
        self.session.execute(
            sql_text(
                """
                UPDATE documents
                SET search_vector =
                    setweight(to_tsvector('danish', coalesce(:title, '')), 'A') ||
                    setweight(to_tsvector('danish', coalesce(:meta, '')), 'B') ||
                    setweight(to_tsvector('danish', coalesce(:cats, '')), 'C') ||
                    setweight(to_tsvector('danish', coalesce(:body, '')), 'D')
                WHERE id = :doc_id
                """
            ),
            {
                "title": " ".join(
                    filter(None, [document.title, document.short_title, document.document_number])
                ),
                "meta": " ".join(filter(None, [document.authority, document.document_type])),
                "cats": category_names,
                "body": content,
                "doc_id": document.id,
            },
        )

    # -- Ændringslog --------------------------------------------------------

    def _log_change(
        self,
        document: Document,
        change_type: ChangeType,
        *,
        old_version_id: int | None = None,
        new_version_id: int | None = None,
        import_run_id: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.session.add(
            ChangeLogEntry(
                document_id=document.id,
                import_run_id=import_run_id,
                old_version_id=old_version_id,
                new_version_id=new_version_id,
                change_type=change_type.value,
                detail=detail,
            )
        )

"""Lagring, opslag og læsning af dokumentrelationer.

Tre opgaver
===========
``sync``
    Ved import: dokumentets udtrukne relationer erstatter de gamle, og hver
    henvisning slås op i ``documents``. Bagefter knyttes ventende relationer
    fra ANDRE dokumenter, der peger på netop dette dokument — så rækkefølgen
    i en import er ligegyldig.

``resolve``
    En henvisning slås op på nummer og år ("LBK 1629/2016"), på
    accessionsnummer eller på lovens titel ("lov om sikkerhed til søs").
    Ved flere træf vælges gældende ret og derefter den nyeste.

``read``
    Historikken (forgængere og efterfølgere, også flere led tilbage),
    hjemmelen og de bekendtgørelser, en lov hjemler — klar til API'et.
    Den modsatte retning af en relation udledes her og gemmes ikke.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.text import fold
from app.models import Document, DocumentRelation
from app.services.ranking import get_ranking_config

from .extract import ExtractedRelation, RelationType, extract_relations, law_title_key

logger = get_logger(__name__)

__all__ = ["RelationRepository", "RelatedDocument", "DocumentRelations", "document_keys"]

#: Hvor mange led historikken følges. Beskytter mod cykler i fejlbehæftede data.
MAX_HISTORY_DEPTH = 50

_TYPE_CODES = {
    "bek": "BEK", "bekendtgoerelse": "BEK",
    "lbk": "LBK", "lovbekendtgoerelse": "LBK",
    "lov": "LOV",
    "vej": "VEJ", "vejledning": "VEJ",
    "cir": "CIR", "cirkulaere": "CIR",
}

_NUMBER_KEY_RE = re.compile(r"^(?:(?P<code>[A-Z]{3}) )?(?P<no>\d+)(?:/(?P<year>\d{4}))?$")


def type_code(document_type: str | None) -> str | None:
    folded = fold(document_type or "").strip(". ")
    return _TYPE_CODES.get(folded) or (folded.upper() or None)


def document_keys(document: Document) -> set[str]:
    """De nøgler, andre dokumenter kan henvise til dette dokument med."""
    keys: set[str] = set()
    year = document.published_date.year if document.published_date else None
    if document.document_number and document.document_number.isdigit():
        number = int(document.document_number)
        code = type_code(document.document_type)
        if year:
            keys.add(f"{number}/{year}")
            if code:
                keys.add(f"{code} {number}/{year}")
    for accn in (document.retsinformation_id, document.source_id):
        if accn:
            keys.add(f"accn:{accn}")
    title_key = law_title_key(document.title)
    if title_key:
        keys.add(title_key)
    return keys


def _title_variants(title_key: str) -> list[str]:
    """"lov om x" -> titlerne loven og dens lovbekendtgørelser bærer."""
    rest = title_key[len("lov "):] if title_key.startswith("lov ") else title_key
    return [f"Lov {rest}", f"Bekendtgørelse af lov {rest}", f"lov {rest}"]


def _preference(document: Document) -> tuple:
    """Gældende før alt andet, derefter nyeste."""
    return (
        0 if fold(document.status or "") == "gaeldende" else 1,
        -(document.published_date.toordinal() if document.published_date else 0),
        -document.id,
    )


def _title_key_of_label(label: str | None) -> str | None:
    """"lov om X, jf. lovbekendtgørelse nr. …" -> "lov om x". Reserve ved hjemmel."""
    if not label:
        return None
    head = label.split(", jf.")[0].strip()
    head = re.sub(r"^lov\s+nr\.\s*\d+\s+af\s+.*?\d{4}\s+", "lov ", head, flags=re.IGNORECASE)
    return head.lower() if head.lower().startswith("lov om ") else None


# -- Læsemodel ----------------------------------------------------------------


@dataclass(slots=True)
class RelatedDocument:
    """Et dokument i en relation — i databasen eller kun som henvisning."""

    id: int | None
    title: str
    label: str
    year: int | None = None
    status: str | None = None
    document_type: str | None = None
    document_number: str | None = None
    section: str | None = None
    sections: list[str] = field(default_factory=list)
    relation_type: str | None = None
    language: str | None = None


@dataclass(slots=True)
class DocumentRelations:
    predecessors: list[RelatedDocument] = field(default_factory=list)
    successors: list[RelatedDocument] = field(default_factory=list)
    #: Bekendtgørelser udstedt med hjemmel i dette dokument (én pr. paragraf).
    delegated_regulations: list[RelatedDocument] = field(default_factory=list)
    #: Den lov, dette dokument er udstedt i medfør af.
    legal_basis: list[RelatedDocument] = field(default_factory=list)
    #: Samme tekst på andre sprog (translation_of, begge retninger).
    translations: list[RelatedDocument] = field(default_factory=list)


def _reference_label(document: Document) -> str:
    """"LBK nr. 1629 af 2016"."""
    code = type_code(document.document_type) or (document.document_type or "")
    parts = [code]
    if document.document_number:
        parts.append(f"nr. {document.document_number}")
    if document.published_date:
        parts.append(f"af {document.published_date.year}")
    return " ".join(p for p in parts if p).strip() or (document.title or "")


def _related(document: Document, **extra) -> RelatedDocument:
    return RelatedDocument(
        id=document.id,
        title=document.display_title or document.title,
        label=_reference_label(document),
        year=document.published_date.year if document.published_date else None,
        status=document.status,
        document_type=document.document_type,
        document_number=document.document_number,
        language=document.language,
        **extra,
    )


def _unresolved(relation: DocumentRelation, **extra) -> RelatedDocument:
    match = _NUMBER_KEY_RE.match(relation.target_reference or "")
    year = int(match.group("year")) if match and match.group("year") else None
    label = relation.target_label or relation.target_reference
    return RelatedDocument(
        id=None,
        title=label,
        label=label,
        year=year,
        document_number=match.group("no") if match else None,
        **extra,
    )


def _public_relation_rows():
    from app.services.rights import legacy_documents
    public_ids = select(Document.id).where(legacy_documents())
    return (
        DocumentRelation.source_document_id.in_(public_ids)
        & or_(DocumentRelation.target_document_id.is_(None), DocumentRelation.target_document_id.in_(public_ids))
    )


class RelationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- Skrivning ------------------------------------------------------------

    def sync(
        self, document: Document, *, content: str | None, raw_metadata: dict | None = None
    ) -> list[DocumentRelation]:
        """Erstatter dokumentets udtrukne relationer og knytter dem til mål."""
        from app.services.rights import RightsService
        if RightsService(self.session).source(document.source) is not None:
            # International relationships require their own reviewed milestone.
            # Do not derive public labels from privately stored source text.
            self.session.execute(delete(DocumentRelation).where(DocumentRelation.source_document_id == document.id))
            self.session.flush()
            return []
        extracted = extract_relations(
            title=document.title,
            content=content,
            is_repeal_act=get_ranking_config().is_repeal_title(document.title),
            raw_metadata=raw_metadata,
            own_keys=document_keys(document),
        )
        # Kun de udtrukne typer erstattes. En oversættelsesrelation
        # (translation_of) vedligeholdes af importen og må ikke forsvinde,
        # fordi relationerne genopbygges.
        self.session.execute(
            delete(DocumentRelation).where(
                DocumentRelation.source_document_id == document.id,
                DocumentRelation.relation_type.in_(RelationType.EXTRACTED),
            )
        )
        rows = [self._row(document, relation) for relation in extracted]
        self.session.add_all(rows)
        self.session.flush()
        self.resolve_inbound(document)
        return rows

    def _row(self, document: Document, relation: ExtractedRelation) -> DocumentRelation:
        target = self.resolve(relation.target_reference, relation.target_label, relation.relation_type)
        if target is not None and target.id == document.id:
            target = None
        return DocumentRelation(
            source_document_id=document.id,
            target_document_id=target.id if target is not None else None,
            target_reference=relation.target_reference[:255],
            target_label=(relation.target_label or "")[:512] or None,
            relation_type=relation.relation_type,
            specific_section=relation.specific_section,
        )

    def resolve(
        self, reference: str, label: str | None = None, relation_type: str | None = None
    ) -> Document | None:
        """Måldokumentet for en henvisning, eller None."""
        found = self._resolve_key(reference)
        if found is None and relation_type == RelationType.EMPOWERED_BY:
            # "lov om X, jf. lovbekendtgørelse nr. 1629 af 2016": findes netop
            # den lovbekendtgørelse ikke, er lovens gældende version målet.
            title_key = _title_key_of_label(label)
            if title_key and title_key != reference:
                found = self._resolve_key(title_key)
        return found

    def _resolve_key(self, key: str) -> Document | None:
        from app.services.rights import legacy_documents
        if key.startswith("accn:"):
            accn = key[len("accn:"):]
            candidates = self.session.scalars(
                select(Document).where(
                    or_(Document.retsinformation_id == accn, Document.source_id == accn), legacy_documents()
                )
            ).all()
        elif (match := _NUMBER_KEY_RE.match(key)) is not None:
            number = match.group("no")
            candidates = [
                d
                for d in self.session.scalars(
                    select(Document).where(Document.document_number.in_({number, number.zfill(4)}), legacy_documents())
                ).all()
                if (not match.group("year") or (d.published_date and d.published_date.year == int(match.group("year"))))
                and (not match.group("code") or type_code(d.document_type) == match.group("code"))
            ]
        elif key.startswith("lov "):
            candidates = self.session.scalars(
                select(Document).where(Document.title.in_(_title_variants(key)), legacy_documents())
            ).all()
        else:
            return None
        return min(candidates, key=_preference) if candidates else None

    def resolve_inbound(self, document: Document) -> int:
        """Knytter andre dokumenters henvisninger til dette dokument.

        To slags:

        * Henvisninger, hvis nøgle ER dette dokuments ("LBK 700/2019"). De
          knyttes, også hvis de allerede peger et andet sted hen: et præcist
          nummer vinder altid over reserven på lovens titel, som kan være
          brugt, mens det præcise dokument endnu ikke var importeret.
        * Ventende hjemmel, hvis lovtitel passer, når nummeret ikke findes.
        """
        from app.services.rights import RightsService
        if RightsService(self.session).source(document.source) is not None:
            return 0
        keys = document_keys(document)
        linked = 0

        exact = self.session.scalars(
            select(DocumentRelation).where(
                _public_relation_rows(),
                DocumentRelation.target_reference.in_(keys),
                DocumentRelation.source_document_id != document.id,
                or_(
                    DocumentRelation.target_document_id.is_(None),
                    DocumentRelation.target_document_id != document.id,
                ),
            )
        ).all()
        for relation in exact:
            target = self._resolve_key(relation.target_reference)
            if target is not None and target.id != relation.source_document_id:
                if relation.target_document_id != target.id:
                    relation.target_document_id = target.id
                    linked += 1

        title_key = law_title_key(document.title)
        if title_key:
            # Hjemmel med et lovbekendtgørelsesnummer, vi ikke har, men en
            # titel, der passer: "lov om X, jf. lovbekendtgørelse nr. …".
            pending = self.session.scalars(
                select(DocumentRelation).where(
                    _public_relation_rows(),
                    DocumentRelation.target_document_id.is_(None),
                    DocumentRelation.source_document_id != document.id,
                    DocumentRelation.relation_type == RelationType.EMPOWERED_BY,
                    or_(
                        DocumentRelation.target_label == title_key,
                        DocumentRelation.target_label.like(f"{title_key},%"),
                    ),
                )
            ).all()
            for relation in pending:
                target = self.resolve(
                    relation.target_reference, relation.target_label, relation.relation_type
                )
                if target is not None and target.id != relation.source_document_id:
                    relation.target_document_id = target.id
                    linked += 1

        if linked:
            self.session.flush()
        return linked

    def link_translation(self, translation: Document, original: Document | None) -> None:
        """Binder et dansk dokument til den autentiske tekst, det oversætter.

        `translation` er den danske bekendtgørelse, `original` det engelske
        traktatdokument. Er der ingen original (genparsningen fandt ingen
        engelsk spalte), fjernes en eventuel tidligere relation.
        """
        self.session.execute(
            delete(DocumentRelation).where(
                DocumentRelation.source_document_id == translation.id,
                DocumentRelation.relation_type == RelationType.TRANSLATION_OF,
            )
        )
        if original is not None:
            self.session.add(
                DocumentRelation(
                    source_document_id=translation.id,
                    target_document_id=original.id,
                    target_reference=f"accn:{original.source_id}"[:255],
                    target_label=(original.title or "")[:512] or None,
                    relation_type=RelationType.TRANSLATION_OF,
                )
            )
        self.session.flush()

    def translations(self, document: Document) -> list[Document]:
        """Samme tekst på andre sprog — i begge retninger af translation_of."""
        outgoing = self.session.scalars(
            select(Document).join(
                DocumentRelation, DocumentRelation.target_document_id == Document.id
            ).where(
                DocumentRelation.source_document_id == document.id,
                DocumentRelation.relation_type == RelationType.TRANSLATION_OF,
            )
        ).all()
        incoming = self.session.scalars(
            select(Document).join(
                DocumentRelation, DocumentRelation.source_document_id == Document.id
            ).where(
                DocumentRelation.target_document_id == document.id,
                DocumentRelation.relation_type == RelationType.TRANSLATION_OF,
            )
        ).all()
        seen: dict[int, Document] = {}
        for other in [*outgoing, *incoming]:
            if other.id != document.id:
                seen.setdefault(other.id, other)
        return sorted(seen.values(), key=lambda d: (d.language or "", d.id))

    # -- Læsning --------------------------------------------------------------

    def read(self, document: Document) -> DocumentRelations:
        result = DocumentRelations()
        from app.services.rights import RightsService
        if RightsService(self.session).source(document.source) is not None:
            return result
        result.predecessors = self._walk(document, backwards=True)
        result.successors = self._walk(document, backwards=False)
        result.legal_basis = self._legal_basis(document)
        result.delegated_regulations = self._delegated(document, result.predecessors, result.successors)
        result.translations = [_related(other) for other in self.translations(document)]
        return result

    def _outgoing(self, document_id: int, relation_type: str) -> list[DocumentRelation]:
        return list(self.session.scalars(
            select(DocumentRelation).where(
                _public_relation_rows(),
                DocumentRelation.source_document_id == document_id,
                DocumentRelation.relation_type == relation_type,
            ).order_by(DocumentRelation.id)
        ))

    def _incoming(self, document_id: int, relation_type: str) -> list[DocumentRelation]:
        return list(self.session.scalars(
            select(DocumentRelation).where(
                _public_relation_rows(),
                DocumentRelation.target_document_id == document_id,
                DocumentRelation.relation_type == relation_type,
            ).order_by(DocumentRelation.id)
        ))

    def _walk(self, document: Document, *, backwards: bool) -> list[RelatedDocument]:
        """Forgængere (backwards) eller efterfølgere, alle led, ældste først.

        Bagud: dette dokument "afløser" sit mål, eller et andet dokument er
        "afløst af" dette. Fremad er det omvendte. Henvisninger til
        dokumenter, der ikke er importeret, bliver endeled uden link.
        """
        forward_type, inverse_type = (
            (RelationType.REPLACES, RelationType.REPLACED_BY)
            if backwards
            else (RelationType.REPLACED_BY, RelationType.REPLACES)
        )
        seen: set[int] = {document.id}
        found: list[RelatedDocument] = []
        unresolved_seen: set[str] = set()
        queue: deque[tuple[int, int]] = deque([(document.id, 0)])
        while queue:
            current_id, depth = queue.popleft()
            if depth >= MAX_HISTORY_DEPTH:
                continue
            next_documents: list[Document] = []
            for relation in self._outgoing(current_id, forward_type):
                if relation.target_document is not None:
                    next_documents.append(relation.target_document)
                elif relation.target_reference not in unresolved_seen:
                    unresolved_seen.add(relation.target_reference)
                    found.append(_unresolved(relation, relation_type=forward_type))
            for relation in self._incoming(current_id, inverse_type):
                next_documents.append(relation.source_document)
            for related in next_documents:
                if related.id in seen:
                    continue
                seen.add(related.id)
                found.append(_related(related, relation_type=forward_type))
                queue.append((related.id, depth + 1))
        found.sort(key=lambda r: (r.year or 0, r.id or 0))
        return found

    def _legal_basis(self, document: Document) -> list[RelatedDocument]:
        """Loven bag en bekendtgørelse, med de paragraffer der er henvist til."""
        grouped: dict[object, RelatedDocument] = {}
        for relation in self._outgoing(document.id, RelationType.EMPOWERED_BY):
            key = relation.target_document_id or relation.target_reference
            entry = grouped.get(key)
            if entry is None:
                entry = (
                    _related(relation.target_document)
                    if relation.target_document is not None
                    else _unresolved(relation)
                )
                if relation.target_label:
                    entry.title = relation.target_label
                grouped[key] = entry
            if relation.specific_section and relation.specific_section not in entry.sections:
                entry.sections.append(relation.specific_section)
        return list(grouped.values())

    def _delegated(
        self,
        document: Document,
        predecessors: list[RelatedDocument],
        successors: list[RelatedDocument],
    ) -> list[RelatedDocument]:
        """Bekendtgørelser udstedt med hjemmel i denne lov — i alle dens versioner.

        En bekendtgørelse udstedt i medfør af lovbekendtgørelse nr. 1629 af
        2016 er stadig udstedt i medfør af loven, efter at loven er samlet
        på ny. Derfor tælles hele lovens historik og alle dokumenter med
        samme lovtitel med.
        """
        family = {document.id}
        family.update(r.id for r in predecessors if r.id)
        family.update(r.id for r in successors if r.id)
        title_key = law_title_key(document.title)
        if title_key:
            from app.services.rights import legacy_documents
            family.update(self.session.scalars(
                select(Document.id).where(Document.title.in_(_title_variants(title_key)), legacy_documents())
            ))

        relations = self.session.scalars(
            select(DocumentRelation).where(
                _public_relation_rows(),
                DocumentRelation.relation_type == RelationType.EMPOWERED_BY,
                DocumentRelation.target_document_id.in_(family),
            ).order_by(DocumentRelation.id)
        ).all()

        entries: list[RelatedDocument] = []
        seen: set[tuple[int, str | None]] = set()
        for relation in relations:
            source = relation.source_document
            identity = (source.id, relation.specific_section)
            if identity in seen or source.id == document.id:
                continue
            seen.add(identity)
            entries.append(_related(source, section=relation.specific_section))
        entries.sort(key=lambda r: (_section_sort_key(r.section), -(r.year or 0), r.id or 0))
        return entries


def _section_sort_key(section: str | None) -> tuple:
    """"§ 3, stk. 2" -> (3, "", 2). Uden paragraf sidst."""
    if not section:
        return (10**9, "", 0)
    match = re.match(r"§\s*(\d+)\s*([a-zæøå]?)(?:,\s*stk\.\s*(\d+))?", section)
    if not match:
        return (10**9 - 1, section, 0)
    return (int(match.group(1)), match.group(2) or "", int(match.group(3) or 0))

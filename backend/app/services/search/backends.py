"""Søgeimplementeringer.

`PostgresSearchBackend` bruger PostgreSQL fuldtekstsøgning med en vægtet
tsvector (A=titel/nummer, B=myndighed/type, C=kategorier, D=brødtekst),
så et titelmatch rangerer højere end et match langt inde i lovteksten.

`FallbackSearchBackend` giver samme funktionalitet på SQLite ved at
score tokens mod `search_text`, titel og myndighed. Den er langsommere
og mindre sproglig, men gør hele applikationen kørbar uden PostgreSQL.

Begge deler filtrering og sortering, så et filter kun findes ét sted.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Float,
    Select,
    and_,
    case,
    func,
    literal,
    literal_column,
    or_,
    select,
)
from sqlalchemy.orm import Session, selectinload

from app.core.logging import get_logger
from app.core.text import make_snippet, tokenize
from app.models import Category, Document, DocumentCategory

from .base import SEARCH_MODES, SearchBackend, SearchHit, SearchQuery, SearchResults

logger = get_logger(__name__)

__all__ = [
    "PostgresSearchBackend",
    "FallbackSearchBackend",
    "get_lexical_backend",
    "get_search_backend",
    "resolve_search_mode",
]

#: PostgreSQL's indbyggede danske ordbog. Håndterer stammer og stopord.
DANISH_CONFIG = "danish"


def _apply_filters(stmt: Select, query: SearchQuery) -> Select:
    """Tilføjer alle facetfiltre. Fælles for begge backends."""
    conditions = []

    if query.document_types:
        conditions.append(Document.document_type.in_(query.document_types))
    if query.authorities:
        conditions.append(Document.authority.in_(query.authorities))
    if query.statuses:
        conditions.append(Document.status.in_(query.statuses))
    if query.document_number:
        conditions.append(Document.document_number == query.document_number)
    if query.published_from is not None:
        conditions.append(Document.published_date >= query.published_from)
    if query.published_to is not None:
        conditions.append(Document.published_date <= query.published_to)
    if query.min_score is not None:
        conditions.append(Document.maritime_score >= query.min_score)
    if query.max_score is not None:
        conditions.append(Document.maritime_score <= query.max_score)
    if query.is_maritime is not None:
        conditions.append(Document.is_maritime.is_(query.is_maritime))
    if query.law_classes:
        conditions.append(Document.law_class.in_(query.law_classes))

    if conditions:
        stmt = stmt.where(and_(*conditions))

    if query.categories:
        # Dokumentet skal have mindst én af de valgte kategorier.
        stmt = stmt.where(
            Document.id.in_(
                select(DocumentCategory.document_id)
                .join(Category, Category.id == DocumentCategory.category_id)
                .where(Category.slug.in_(query.categories))
            )
        )

    return stmt


def _apply_sort(stmt: Select, query: SearchQuery, rank_column: Any) -> Select:
    """Sorterer resultatet. Relevanssortering kræver en søgestreng."""
    if query.sort == "date_desc":
        return stmt.order_by(Document.published_date.desc().nullslast(), Document.id.desc())
    if query.sort == "date_asc":
        return stmt.order_by(Document.published_date.asc().nullsfirst(), Document.id.asc())
    if query.sort == "score_desc":
        return stmt.order_by(Document.maritime_score.desc(), Document.id.desc())
    if query.sort == "title":
        return stmt.order_by(Document.title.asc())

    # Relevans uden søgestreng er en gennemsynsliste, og der skal de
    # centrale love stå først. Rækkefølgen udtrykkes direkte i SQL frem for
    # at rangere i Python, så `total` forbliver rigtigt og sideinddelingen
    # dækker hele databasen — ikke kun de første 200 kandidater.
    if rank_column is None:
        return stmt.order_by(
            _law_class_priority(),
            _status_priority(),
            Document.maritime_score.desc(),
            Document.scope_score.desc(),
            Document.published_date.desc().nullslast(),
            Document.id.desc(),
        )
    return stmt.order_by(rank_column.desc(), Document.maritime_score.desc(), Document.id.desc())


def _law_class_priority():
    """Kernelov før speciallov før støttedokument."""
    return case(
        (Document.law_class == "kernelaw", 0),
        (Document.law_class == "speciallaw", 1),
        (Document.law_class == "support", 2),
        else_=1,
    )


def _status_priority():
    """Gældende ret før historisk og ophævet."""
    return case(
        (Document.status == "Gældende", 0),
        (Document.status == "Fremtidig", 1),
        (Document.status == "Historisk", 3),
        (Document.status == "Ophævet", 4),
        else_=2,
    )


def _load_options():
    """Ivrig indlæsning, så API-laget ikke udløser N+1-forespørgsler."""
    return (
        selectinload(Document.category_links).selectinload(DocumentCategory.category),
        selectinload(Document.current_version),
    )


def _snippet_for(document: Document, terms: list[str]) -> str:
    """Uddrag fra dokumentets aktuelle version."""
    version = document.current_version
    if version is None or not version.content:
        return ""
    return make_snippet(version.content, terms)


def _paginate(session: Session, stmt: Select, query: SearchQuery, terms: list[str],
              backend_name: str) -> SearchResults:
    """Fælles optælling, sideinddeling og resultatopbygning."""
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = session.scalar(count_stmt) or 0

    stmt = stmt.offset(query.offset).limit(query.page_size)
    hits = [
        SearchHit(
            document=row[0],
            rank=float(row[1] or 0.0),
            snippet=_snippet_for(row[0], terms),
            lexical_rank=float(row[1] or 0.0),
            match_source="lexical",
        )
        for row in session.execute(stmt).all()
    ]
    return SearchResults(
        hits=hits,
        total=total,
        page=query.page,
        page_size=query.page_size,
        backend=backend_name,
        mode="lexical",
    )


class PostgresSearchBackend:
    """Fuldtekstsøgning med PostgreSQL."""

    name = "postgresql"

    def search(self, session: Session, query: SearchQuery) -> SearchResults:
        search_terms = query.q.strip() if query.q else ""
        rank_column = None
        stmt = select(Document).options(*_load_options())

        if search_terms:
            # websearch_to_tsquery forstår almindelig søgesyntaks
            # (citater, OR, -udeluk) og fejler ikke på skæve input.
            tsquery = func.websearch_to_tsquery(DANISH_CONFIG, search_terms)
            # literal_column (ikke text()) — kun et kolonneudtryk understøtter
            # operatoren @@. Kolonnen findes kun på PostgreSQL og oprettes
            # betinget i migrationen.
            vector = literal_column("documents.search_vector")
            rank_column = func.ts_rank_cd(vector, tsquery)
            # Dokumentnummer er ikke naturligt sprog og fanges ikke altid af
            # tsquery. Praktikere søger ofte direkte på nummeret.
            stmt = stmt.where(
                or_(vector.op("@@")(tsquery), Document.document_number == search_terms)
            )
            stmt = stmt.add_columns(rank_column.label("rank"))
        else:
            stmt = stmt.add_columns(literal(0.0).label("rank"))

        stmt = _apply_filters(stmt, query)
        stmt = _apply_sort(stmt, query, rank_column)
        return _paginate(session, stmt, query, tokenize(search_terms), self.name)


class FallbackSearchBackend:
    """Portabel søgning uden databasespecifikke funktioner.

    Bruges på SQLite. Hvert token skal optræde i dokumentets søgetekst
    (AND-semantik). Rangeringen vægter titelmatch højest, så adfærden
    ligner Postgres-backendens.
    """

    name = "fallback"

    #: Titelmatch vejer tungest, ligesom setweight('A') i Postgres-backenden.
    TITLE_WEIGHT = 10.0
    BODY_WEIGHT = 1.0

    def search(self, session: Session, query: SearchQuery) -> SearchResults:
        search_terms = query.q.strip() if query.q else ""
        tokens = tokenize(search_terms) if search_terms else []
        rank_column = None
        stmt = select(Document).options(*_load_options())

        if tokens:
            token_conditions = []
            rank_expr = literal(0.0)

            for token in tokens:
                pattern = f"%{token}%"
                # search_text og search_title er allerede foldet og
                # lowercased ved indeksering, ligesom tokens er det.
                token_conditions.append(Document.search_text.like(pattern))
                # case() giver et portabelt tal ud af en boolsk test.
                rank_expr = (
                    rank_expr
                    + case(
                        (
                            func.coalesce(Document.search_title, "").like(pattern),
                            literal(self.TITLE_WEIGHT),
                        ),
                        else_=literal(0.0),
                    )
                    + case(
                        (
                            func.coalesce(Document.search_text, "").like(f"%{token}%"),
                            literal(self.BODY_WEIGHT),
                        ),
                        else_=literal(0.0),
                    )
                )

            # Alle tokens skal findes, ELLER strengen er et dokumentnummer.
            stmt = stmt.where(
                or_(and_(*token_conditions), Document.document_number == search_terms)
            )
            rank_column = func.cast(rank_expr, Float)
            stmt = stmt.add_columns(rank_column.label("rank"))
        else:
            stmt = stmt.add_columns(literal(0.0).label("rank"))

        stmt = _apply_filters(stmt, query)
        stmt = _apply_sort(stmt, query, rank_column)
        return _paginate(session, stmt, query, tokens, self.name)


def get_lexical_backend(session: Session) -> SearchBackend:
    """Vælger den leksikalske backend ud fra den forbundne database."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return PostgresSearchBackend()
    return FallbackSearchBackend()


def resolve_search_mode(session: Session, requested: str | None = None) -> tuple[str, str | None]:
    """Afgør hvilken tilstand der FAKTISK kan leveres.

    Returnerer (tilstand, besked). Beskeden er ikke None, når den ønskede
    tilstand ikke kunne leveres — den vises i brugerfladen frem for at
    lade brugeren tro, at der blev søgt semantisk.

    Nedgraderingen er tavs over for brugeren i den forstand, at søgningen
    stadig virker; men den er ALDRIG tavs i svaret. En bruger der tror
    systemet leder efter synonymer, og som derfor konkluderer at der ikke
    findes regler om emnet, er værre stillet end en der får at vide, at
    kun ordene blev slået op.
    """
    from app.core.config import get_settings

    settings = get_settings()
    mode = (requested or settings.search_default_mode or "lexical").strip().lower()
    if mode not in SEARCH_MODES:
        mode = "lexical"
    if mode == "lexical":
        return "lexical", None

    if not settings.embeddings_enabled:
        return "lexical", "Vektorsøgning er slået fra i konfigurationen (EMBEDDINGS_ENABLED)."

    try:
        from .vector import VectorSearchBackend

        if not VectorSearchBackend().has_vectors(session):
            return (
                "lexical",
                "Der findes endnu ingen vektorer. Kør 'python -m app.cli embed run' "
                "for at slå betydningssøgning til.",
            )
    except Exception as exc:  # noqa: BLE001 - modelfejl må ikke blokere søgning
        logger.warning("search.mode.vector_unavailable", extra={"error": str(exc)})
        return "lexical", "Embedding-modellen er ikke tilgængelig. Der blev søgt leksikalsk."

    return mode, None


def get_search_backend(session: Session, mode: str = "lexical") -> SearchBackend:
    """Backend for den ønskede tilstand.

    `mode` forudsættes allerede afklaret med :func:`resolve_search_mode`;
    denne funktion gætter ikke og falder ikke tilbage.

    Alle tre tilstande går gennem :class:`RankedSearchBackend`. Det er et
    bevidst valg: rangeringsmodellen — kernelove før speciallove, gældende
    før historisk — er domæneviden om lovstof, ikke en egenskab ved
    vektorindekset. En bruger, der skifter fra "ordret" til "kombineret",
    skal få flere resultater, ikke en anden opfattelse af hvad der er en
    central lov.
    """
    lexical = get_lexical_backend(session)

    from .hybrid import RankedSearchBackend

    if mode == "lexical":
        return RankedSearchBackend(lexical, None, mode="lexical")

    from .vector import VectorSearchBackend

    vector = VectorSearchBackend()
    return RankedSearchBackend(lexical, vector, mode=mode)

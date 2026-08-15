"""SQLAlchemy-modeller for den maritime lovdatabase.

Skemaoversigt::

    documents ──1:N──> document_versions
        │  └──current_version_id──> document_versions (aktuel version)
        ├──M:N──> categories       (via document_categories)
        ├──1:N──> document_chunks  (vektoriseret lovtekst)
        └──1:N──> change_log

    import_runs ──1:N──> change_log

    search_queries   (logget og vektoriseret søgehistorik — står alene)
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow

__all__ = [
    "Document",
    "DocumentVersion",
    "Category",
    "DocumentCategory",
    "ImportRun",
    "ChangeLogEntry",
    "ChangeType",
    "ImportStatus",
    "BackfillManifestItem",
    "BackfillStatus",
    "CuratedRelevanceOverride",
    "CuratedRelevanceOverrideEvent",
    "CuratedDecision",
    "CuratedOverrideEventType",
    "DocumentChunk",
    "SearchQueryLog",
]


class ChangeType(str, enum.Enum):
    """Typer af registrerede ændringer."""

    CREATED = "CREATED"
    CONTENT_UPDATED = "CONTENT_UPDATED"
    METADATA_UPDATED = "METADATA_UPDATED"
    STATUS_CHANGED = "STATUS_CHANGED"


class ImportStatus(str, enum.Enum):
    """Status for en importkørsel."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"


class BackfillStatus(str, enum.Enum):
    """Tilstand for en post i efterindlæsningskøen."""

    #: Venter på at blive taget af en arbejder.
    PENDING = "PENDING"
    #: Reserveret af en arbejder. Reservationen har en udløbstid.
    PROCESSING = "PROCESSING"
    #: Midlertidig fejl. Forsøges igen efter `next_attempt_at`.
    RETRY = "RETRY"
    #: Endeligt mislykket — permanent fejl eller forsøgene er brugt op.
    FAILED = "FAILED"
    #: Hentet og gemt.
    COMPLETED = "COMPLETED"
    #: Hentet, men under den maritime lagringstærskel. Endeligt.
    REJECTED = "REJECTED"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)

    @classmethod
    def terminal(cls) -> tuple["BackfillStatus", ...]:
        return (cls.FAILED, cls.COMPLETED, cls.REJECTED)


# ---------------------------------------------------------------------------
# Dokumenter
# ---------------------------------------------------------------------------


class Document(Base):
    """Det logiske juridiske dokument, uafhængigt af dets versioner.

    Indholdet ligger i :class:`DocumentVersion`. Denne tabel bærer
    identitet, normaliseret metadata, maritim klassifikation og en
    peger til den aktuelle version.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- Herkomst -----------------------------------------------------------
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="retsinformation")
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    # Retsinformations accessionsnummer, f.eks. "B20220122005".
    retsinformation_id: Mapped[str | None] = mapped_column(String(128))
    # Lov-/bekendtgørelsesnummer som brugeren kender det, f.eks. "1290".
    # Søgbart selvstændigt felt, da praktikere ofte søger på nummer.
    document_number: Mapped[str | None] = mapped_column(String(64), index=True)
    # True når dokumentet stammer fra en fixturkilde. Systemet må ALDRIG
    # præsentere syntetiske data som var de hentet fra Retsinformation.
    is_synthetic: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)

    # --- Normaliseret metadata ---------------------------------------------
    title: Mapped[str] = mapped_column(Text, nullable=False)
    short_title: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str | None] = mapped_column(String(128), index=True)
    authority: Mapped[str | None] = mapped_column(String(256), index=True)
    published_date: Mapped[date | None] = mapped_column(Date, index=True)
    effective_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(64), index=True)

    # --- Maritim klassifikation --------------------------------------------
    is_maritime: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    maritime_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    # Matchede termer, negative signaler og begrundelse fra relevansmotoren.
    relevance_details: Mapped[dict | None] = mapped_column(JSON)
    # Navnet på den motor der foretog vurderingen ("keyword", senere "hybrid").
    relevance_engine: Mapped[str | None] = mapped_column(String(64))
    # Hvilken dokumentversion vurderingen blev beregnet på. Uden denne
    # kan man ikke efterprøve en klassifikation af en lovtekst der siden
    # er ændret.
    relevance_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    # --- Søgning ------------------------------------------------------------
    # Flade, portable søgefelter. Begge gemmes FOLDET (æ->ae, ø->oe,
    # å->aa, små bogstaver), så søgning er uafhængig af om brugeren
    # skriver "søulykke" eller "soeulykke".
    # PostgreSQL bruger derudover kolonnen `search_vector` (tsvector),
    # som tilføjes i migrationen og opdateres samme sted.
    search_text: Mapped[str | None] = mapped_column(Text)
    #: Kun titel, korttitel og dokumentnummer — bruges til titelrangering.
    search_title: Mapped[str | None] = mapped_column(Text)

    # --- Semantisk indeks ---------------------------------------------------
    # Hvilken version der er vektoriseret. Er den forskellig fra
    # `current_version_id`, er vektorerne forældede. Dermed behøves ingen
    # tilstandsmaskine: et dokument mangler vektorer, hvis og kun hvis
    # embedded_version_id != current_version_id eller modellen er skiftet.
    embedded_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    #: Modellen der lavede vektorerne. Skifter modellen, skal alt bygges om.
    embedding_model: Mapped[str | None] = mapped_column(String(128), index=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- Versionspeger ------------------------------------------------------
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    # --- Tidsstempler -------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    last_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Relationer ---------------------------------------------------------
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="DocumentVersion.document_id",
        order_by="DocumentVersion.version_number",
        passive_deletes=True,
    )
    current_version: Mapped["DocumentVersion | None"] = relationship(
        foreign_keys=[current_version_id],
        post_update=True,
        viewonly=True,
    )
    relevance_version: Mapped["DocumentVersion | None"] = relationship(
        foreign_keys=[relevance_version_id],
        post_update=True,
        viewonly=True,
    )
    embedded_version: Mapped["DocumentVersion | None"] = relationship(
        foreign_keys=[embedded_version_id],
        post_update=True,
        viewonly=True,
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentChunk.chunk_index",
        passive_deletes=True,
    )
    category_links: Mapped[list["DocumentCategory"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    change_log: Mapped[list["ChangeLogEntry"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ChangeLogEntry.created_at.desc()",
        passive_deletes=True,
    )

    __table_args__ = (
        # Et kildedokument må kun findes én gang pr. kilde.
        UniqueConstraint("source", "source_id", name="uq_documents_source_source_id"),
        Index("ix_documents_retsinformation_id", "retsinformation_id"),
        Index("ix_documents_maritime_lookup", "is_maritime", "maritime_score"),
        # BEMÆRK: intet eksplicit Index for `document_number` her.
        # Kolonnen har allerede `index=True`, og navnekonventionen
        # "ix_%(column_0_label)s" giver præcis ix_documents_document_number
        # — altså samme navn. Begge definitioner gav to Index-objekter i
        # metadata og dermed en "index already exists"-fejl ved
        # Base.metadata.create_all() mod en tom database. Migration
        # 0001 opretter indekset én gang og var altid korrekt; det var
        # modellen, der talte dobbelt.

        CheckConstraint(
            "maritime_score >= 0 AND maritime_score <= 100",
            name="maritime_score_range",
        ),
    )

    @property
    def categories(self) -> list["Category"]:
        return [link.category for link in self.category_links]

    def needs_embedding(self, model: str) -> bool:
        """Er dokumentets vektorer forældede i forhold til `model`?

        Sandt hvis der aldrig er vektoriseret, hvis indholdet er kommet i
        en ny version siden, eller hvis vektorerne stammer fra en anden
        model end den der er i brug nu.
        """
        if self.current_version_id is None:
            return False  # intet indhold at vektorisere
        if self.embedded_version_id != self.current_version_id:
            return True
        return (self.embedding_model or "") != model

    def __repr__(self) -> str:  # pragma: no cover - kun til fejlsøgning
        return f"<Document id={self.id} source_id={self.source_id!r} score={self.maritime_score}>"


class DocumentVersion(Base):
    """Et uforanderligt øjebliksbillede af et dokuments indhold.

    Historiske versioner overskrives aldrig. Ved indholdsændring
    oprettes en ny række med næste versionsnummer.
    """

    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # SHA-256 over normaliseret indhold.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # SHA-256 over normaliseret metadata — adskiller METADATA_UPDATED fra CONTENT_UPDATED.
    metadata_hash: Mapped[str | None] = mapped_column(String(64))
    # Rå metadata fra kilden, bevaret uændret af hensyn til sporbarhed.
    metadata_json: Mapped[dict | None] = mapped_column(JSON)

    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    document: Mapped["Document"] = relationship(
        back_populates="versions",
        foreign_keys=[document_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_document_id_version"
        ),
        Index("ix_document_versions_document_version", "document_id", "version_number"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DocumentVersion doc={self.document_id} v={self.version_number}>"


# ---------------------------------------------------------------------------
# Kategorier
# ---------------------------------------------------------------------------


class Category(Base):
    """Maritim emnekategori. Seedes fra config/categories.yaml."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    document_links: Mapped[list["DocumentCategory"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Category {self.slug}>"


class DocumentCategory(Base):
    """Kobling mellem dokument og kategori med tildelingssikkerhed."""

    __tablename__ = "document_categories"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    # 0.0–1.0. Meningsfuld allerede med regelbaseret tildeling og
    # klar til senere AI-baseret kategorisering.
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    matched_terms: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    document: Mapped["Document"] = relationship(back_populates="category_links")
    category: Mapped["Category"] = relationship(back_populates="document_links")

    __table_args__ = (
        Index("ix_document_categories_category_id", "category_id"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_range"),
    )


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


class ImportRun(Base):
    """Registrering af en importkørsel."""

    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="retsinformation")
    # Hvilken klient der blev brugt: "fixture" eller "production".
    client_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="fixture")
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documents_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ImportStatus.RUNNING.value, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    # Pr-dokument fejldetaljer: [{"source_id": ..., "error": ...}, ...]
    errors: Mapped[list | None] = mapped_column(JSON)

    change_entries: Mapped[list["ChangeLogEntry"]] = relationship(back_populates="import_run")

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ImportRun id={self.id} status={self.status}>"


class ChangeLogEntry(Base):
    """Let ændringslog. Version 1 registrerer at noget ændrede sig,
    ikke en juridisk fortolkning af ændringen."""

    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_runs.id", ondelete="SET NULL"), index=True
    )
    old_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    new_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    change_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    document: Mapped["Document"] = relationship(back_populates="change_log")
    import_run: Mapped["ImportRun | None"] = relationship(back_populates="change_entries")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChangeLogEntry doc={self.document_id} type={self.change_type}>"


class BackfillManifestItem(Base):
    """Én kø-post i den historiske efterindlæsning.

    Retsinformations ændringsfeed rækker kun ti dage tilbage, så ældre
    lovgivning kan udelukkende hentes ved at slå bestemte
    accessionsnumre op. Denne tabel er arbejdslisten over de numre og
    holder styr på hvor langt vi er nået.

    Samtidighed
    ===========
    En arbejder *reserverer* en post: status sættes til PROCESSING, der
    skrives et `claim_token` og en `lease_expires_at`. Udløber
    reservationen, må en anden arbejder tage posten. Den første arbejder
    kan derfor stadig være i gang — derfor skrives enhver efterfølgende
    statusændring med `claim_token` i WHERE-klausulen (et fencing token),
    så en forsinket arbejder ikke overskriver den nye ejers tilstand.

    Selve dokumentskrivningen er indholds-hashet i
    `DocumentRepository`, så en dobbeltbehandling af samme post giver
    ikke en ekstra version — den giver UNCHANGED.
    """

    __tablename__ = "backfill_manifest_items"

    #: Retsinformations accessionsnummer. Naturlig nøgle: samme dokument
    #: kan ikke stå i køen to gange.
    accession_number: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Hvor posten kom fra: "sofartsstyrelsen-2024", "manual", ...
    source_tag: Mapped[str] = mapped_column(
        String(128), nullable=False, default="manual", index=True
    )
    #: Lavere tal behandles først.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BackfillStatus.PENDING.value, index=True
    )
    #: Fencing token for den aktuelle reservation.
    claim_token: Mapped[str | None] = mapped_column(String(64), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    #: Sat når posten er behandlet: hvilken importkørsel gjorde det.
    import_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_runs.id", ondelete="SET NULL")
    )

    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'RETRY', 'FAILED', 'COMPLETED', 'REJECTED')",
            name="status_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        # Køopslaget sorterer på (status, priority, next_attempt_at).
        Index(
            "ix_backfill_manifest_items_queue",
            "status",
            "priority",
            "next_attempt_at",
        ),
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in {s.value for s in BackfillStatus.terminal()}

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BackfillManifestItem {self.accession_number} "
            f"status={self.status} attempts={self.attempt_count}>"
        )


class CuratedDecision(str, enum.Enum):
    """En menneskelig relevansafgørelse, uafhængig af den automatiske motor."""

    INCLUDE = "include"
    EXCLUDE = "exclude"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


class CuratedRelevanceOverride(Base):
    """Permanent, revisionssikker menneskelig override af relevansvurderingen.

    Findes der en post her for et accessionsnummer, tilsidesætter den
    den *effektive* afgørelse — `documents.is_maritime` og kø-status —
    men rører ALDRIG den automatiske motors egen udregning. Den
    automatiske score, klassifikation og matchede termer forbliver
    urørte i `documents.maritime_score` og `documents.relevance_details`,
    så en manuel beslutning aldrig kan forveksles med at motoren selv nåede
    et andet resultat. En score på 35 forbliver 35, uanset override.

    Nøglen er accessionsnummeret — samme naturlige nøgle som
    :class:`BackfillManifestItem` og :class:`Document.source_id` (for
    kilden "retsinformation"). Der er bevidst ingen fremmednøgle til
    hverken køen eller dokumenttabellen: en kurateret afgørelse kan
    registreres, før dokumentet nogensinde er importeret, og skal
    fortsat gælde, hvis dokumentet senere slettes af køen og
    genindlæses.
    """

    __tablename__ = "curated_relevance_overrides"

    #: Retsinformations accessionsnummer, f.eks. "B20220122005".
    accession_number: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: "include" eller "exclude" — se :class:`CuratedDecision`.
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: Hvor afgørelsen kom fra. "curated" i Version 1 (menneskelig
    #: gennemgang). Feltet findes selvstændigt, så en senere
    #: AI-assisteret gennemgang kan skelnes fra ren manuel kuratering
    #: uden en skemaændring.
    decision_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="curated"
    )
    #: Menneskelig begrundelse. Obligatorisk — en override uden
    #: begrundelse er ikke revisionssikker.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: Hvilken gennemgang/batch afgørelsen stammer fra, f.eks.
    #: "global-discovery-triage-2026-08". Samme felt-semantik som
    #: `BackfillManifestItem.source_tag`.
    source_tag: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    #: Hvem eller hvad traf beslutningen. Valgfri — Version 1 har ingen
    #: brugerkonti, men feltet er klar til det.
    decided_by: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    # Indeksene på `decision` og `source_tag` kommer fra `index=True`
    # ovenfor. De må IKKE også stå som eksplicitte Index(...) her:
    # navnekonventionen "ix_%(column_0_label)s" giver præcis samme navn,
    # så begge definitioner ville give to Index-objekter med samme navn i
    # metadata — og en CREATE INDEX-fejl ved create_all mod en tom base.
    # Se test_curated_relevance_override.TestSkemaOverensstemmelse.
    __table_args__ = (
        CheckConstraint(
            "decision IN ('include', 'exclude')",
            # Kort navn: konventionen er "ck_%(table_name)s_%(constraint_name)s",
            # så dette bliver ck_curated_relevance_overrides_decision_valid —
            # identisk med migration 0003. Samme mønster som
            # BackfillManifestItem.status_valid.
            name="decision_valid",
        ),
    )

    @property
    def is_include(self) -> bool:
        return self.decision == CuratedDecision.INCLUDE.value

    def to_json(self) -> dict:
        """Serialisering til `documents.relevance_details["curated_override"]`."""
        return {
            "decision": self.decision,
            "decision_source": self.decision_source,
            "reason": self.reason,
            "source_tag": self.source_tag,
            "decided_by": self.decided_by,
            "decided_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CuratedRelevanceOverride {self.accession_number} "
            f"decision={self.decision}>"
        )


class CuratedOverrideEventType(str, enum.Enum):
    """Hvilken slags mutation en historikpost registrerer."""

    #: Første afgørelse for dette accessionsnummer.
    CREATED = "CREATED"
    #: include -> exclude eller omvendt.
    DECISION_CHANGED = "DECISION_CHANGED"
    #: Samme afgørelse, men begrundelse, source_tag eller decided_by ændret.
    DETAILS_UPDATED = "DETAILS_UPDATED"
    #: Overriden fjernet — dokumentet falder tilbage til ren automatik.
    CLEARED = "CLEARED"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


class CuratedRelevanceOverrideEvent(Base):
    """Uforanderlig historikpost for hver mutation af en kurateret override.

    :class:`CuratedRelevanceOverride` bærer den *aktuelle* effektive
    afgørelse og overskrives ved ændring. Uden denne tabel ville en
    tidligere beslutning, begrundelse eller source_tag være tabt i samme
    øjeblik nogen rettede den — og en override, der blev fjernet med
    ``clear_override``, ville ikke efterlade noget spor overhovedet. Det
    er præcis dét, "revisionssikker" skal betyde, så historikken ligger
    her.

    Tabellen er **append-only**. Der findes ingen opdaterings- eller
    sletteoperation i :mod:`app.services.curation.overrides`, og der bør
    heller aldrig tilføjes en. Hver række gemmer både den forrige og den
    nye tilstand, så et fuldt forløb kan genskabes ved at læse rækkerne i
    ``created_at``-rækkefølge — også for et accessionsnummer, hvis
    override siden er slettet.

    Ved CLEARED er ``new_decision`` NULL: der er ingen gældende afgørelse
    bagefter. ``previous_decision`` er NULL ved CREATED af samme grund.
    """

    __tablename__ = "curated_relevance_override_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Ingen fremmednøgle til curated_relevance_overrides: historikken
    #: skal overleve, at overriden slettes (CLEARED).
    #: Intet `index=True` her — det sammensatte indeks nedenfor starter med
    #: netop denne kolonne og dækker derfor også opslag på den alene.
    accession_number: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    previous_decision: Mapped[str | None] = mapped_column(String(16))
    new_decision: Mapped[str | None] = mapped_column(String(16))
    previous_reason: Mapped[str | None] = mapped_column(Text)
    new_reason: Mapped[str | None] = mapped_column(Text)
    previous_source_tag: Mapped[str | None] = mapped_column(String(128))
    new_source_tag: Mapped[str | None] = mapped_column(String(128))

    decision_source: Mapped[str | None] = mapped_column(String(32))
    #: Hvem eller hvad udførte netop denne mutation.
    decided_by: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('CREATED', 'DECISION_CHANGED', 'DETAILS_UPDATED', 'CLEARED')",
            name="event_type_valid",
        ),
        # Historikopslaget er altid "alle hændelser for ét accessionsnummer,
        # i tidsrækkefølge".
        Index(
            "ix_curated_override_events_accession_created",
            "accession_number",
            "created_at",
        ),
    )

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "accession_number": self.accession_number,
            "event_type": self.event_type,
            "previous_decision": self.previous_decision,
            "new_decision": self.new_decision,
            "previous_reason": self.previous_reason,
            "new_reason": self.new_reason,
            "previous_source_tag": self.previous_source_tag,
            "new_source_tag": self.new_source_tag,
            "decision_source": self.decision_source,
            "decided_by": self.decided_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CuratedRelevanceOverrideEvent {self.accession_number} "
            f"{self.event_type} {self.previous_decision}->{self.new_decision}>"
        )


# ---------------------------------------------------------------------------
# Semantisk indeks
# ---------------------------------------------------------------------------


class DocumentChunk(Base):
    """Et vektoriseret stykke lovtekst.

    Hvorfor stykker og ikke hele dokumenter: en bekendtgørelse på 80.000
    tegn ville som én vektor blive et gennemsnit af alle sine emner og
    ligne enhver søgning en lille smule. Et stykke svarer så vidt muligt
    til én paragraf — den enhed man faktisk henviser til.

    Vektoren gemmes to steder:

    * `embedding` — float32 little-endian BLOB. Portabel, virker på
      SQLite såvel som PostgreSQL, og er **sandheden**.
    * `embedding_vec` — pgvector-kolonne, kun på PostgreSQL og kun hvis
      udvidelsen findes. Oprettes i migration 0004 og er et indeks over
      BLOB'en, ikke en selvstændig kilde.

    Chunks hører til en bestemt *version*. Ændrer lovteksten sig, slettes
    dokumentets chunks og skrives på ny — historikken ligger i
    `document_versions`, ikke her, og et vektorindeks over tilbagetrukket
    tekst ville kun give forkerte søgeresultater.
    """

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Versionen teksten er taget fra. Uden den kan et hit ikke efterprøves.
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Selve lovteksten, uden det kontekstpræfiks der blev vektoriseret.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Nærmeste overskrift, f.eks. "§ 12" eller "Kapitel 3".
    heading: Mapped[str | None] = mapped_column(String(256))
    #: Placering i versionens tekst — gør det muligt at pege på stedet.
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(128), index=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_id_index"),
        Index("ix_document_chunks_model_lookup", "embedding_model", "document_id"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DocumentChunk doc={self.document_id} #{self.chunk_index} {self.heading or ''}>"


class SearchQueryLog(Base):
    """En søgning som brugerne faktisk har stillet — med sin egen vektor.

    Aggregeret pr. normaliseret søgestreng frem for én række pr.
    tastetryk: tabellen skal kunne besvare "hvad søger folk efter" uden
    at vokse uhæmmet, og "søgt 40 gange, aldrig med resultat" er en mere
    brugbar oplysning end 40 enkeltrækker.

    Vektoren gør tre ting mulige:

    * relaterede søgninger ("andre har også søgt efter ..."),
    * gruppering af søgninger der betyder det samme men er skrevet
      forskelligt,
    * grundlaget for senere spørgsmål/svar over lovteksten.

    Der gemmes ingen bruger-, IP- eller sessionsoplysninger. Tabellen er
    en oversigt over *hvad* der søges efter, ikke over *hvem*.
    """

    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: SHA-256 over den foldede søgestreng. Nøglen der samler gentagelser.
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    #: Ordret som brugeren skrev den (første gang den blev set).
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Foldet form — grundlag for hash og for sammenligning.
    normalized_query: Mapped[str] = mapped_column(Text, nullable=False)

    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(128), index=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)

    #: Antal gange søgningen er stillet.
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    #: Antal træf ved seneste kørsel. 0 = en søgning systemet ikke kunne svare på.
    last_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Højeste antal træf søgningen nogensinde har givet. Adskiller
    #: "findes ikke" fra "gav ingenting på grund af et filter denne gang".
    best_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Søgetilstand ved seneste kørsel: lexical | semantic | hybrid.
    last_mode: Mapped[str | None] = mapped_column(String(16))

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    __table_args__ = (
        Index("ix_search_queries_popularity", "occurrences", "last_seen_at"),
        CheckConstraint("occurrences >= 1", name="occurrences_positive"),
    )

    @property
    def had_no_results(self) -> bool:
        """Søgningen har aldrig givet et eneste resultat."""
        return self.best_result_count == 0

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "query": self.query_text,
            "occurrences": self.occurrences,
            "last_result_count": self.last_result_count,
            "best_result_count": self.best_result_count,
            "last_mode": self.last_mode,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SearchQueryLog {self.query_text!r} x{self.occurrences}>"

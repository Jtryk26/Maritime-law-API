"""Find den paragraf, et søgeresultat faktisk handler om.

Hvorfor det ikke kan klares af vektorindekset alene
===================================================
Vektorsøgningen finder allerede det bedst matchende stykke — men kun for
dokumenter, der er vektoriseret, og kun når der søges semantisk. En ren
leksikalsk søgning, en søgning på et dokumentnummer eller en søgning i en
installation, hvor ``EMBEDDINGS_ENABLED=false``, ville da falde tilbage
til et uddrag "et sted i teksten". Brugeren skal have en paragraf at
henvise til i alle tre tilstande.

Hvordan
=======
For de dokumenter, der står på den viste side — højst 20 ad gangen —
læses den gældende version og parses strukturelt. Paragrafferne scores
mod søgeordene:

.. code-block:: text

    score = antal forskellige søgeord i paragraffen * 10
          + samlet antal forekomster
          + 5   hvis ordet står i paragraffens kapiteloverskrift

Det er bevidst en simpel optælling. Rangeringen mellem *dokumenter* er
allerede afgjort på dette tidspunkt; det eneste der skal afgøres her, er
hvilken paragraf inde i dokumentet brugeren skal se først, og til det er
antallet af søgeord der optræder, et fuldt tilstrækkeligt mål.

Parsingen caches pr. version. En version er uforanderlig, så cachen kan
ikke blive forældet — ændres teksten, opstår der en ny version med et
nyt id.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.text import fold, make_snippet
from app.models import Document, DocumentVersion
from app.services.legal.structure import LegalDocumentStructure, parse_legal_structure

__all__ = ["ParagraphHit", "locate_paragraphs", "structure_for_version", "clear_structure_cache"]

#: Højst så mange versioner holdes parset i hukommelsen.
_STRUCTURE_CACHE_SIZE = 128

#: ... og højst så mange tegn kildetekst tilsammen. En sammenskrevet lov kan
#: være 1,9 MB, og en cachet version holder både den parsede struktur og den
#: foldede tekst pr. paragraf — i alt et par gange kildetekstens størrelse. Et
#: loft på antal alene ville kunne holde 128 af dem pr. worker.
_STRUCTURE_CACHE_BUDGET_CHARS = 24_000_000


@dataclass(slots=True)
class ParagraphHit:
    """Én paragraf fra ét dokument, med begrundelse for at den blev valgt."""

    document_id: int
    paragraph_id: str
    chapter_no: str | None
    chapter_title: str | None
    section_title: str | None
    legal_path: str
    full_citation: str
    snippet: str
    score: float
    char_start: int
    char_end: int

    def to_json(self) -> dict:
        return {
            "paragraph_id": self.paragraph_id,
            "chapter_no": self.chapter_no,
            "chapter_title": self.chapter_title,
            "section_title": self.section_title,
            "legal_path": self.legal_path,
            "full_citation": self.full_citation,
            "snippet": self.snippet,
            "score": round(self.score, 2),
        }


@dataclass(slots=True)
class _CachedVersion:
    structure: LegalDocumentStructure
    size: int
    #: Foldet brødtekst og kapitel-/overskriftskontekst pr. paragraf, i samme
    #: rækkefølge som ``structure.paragraphs``. Dannes først ved behov.
    folded: tuple[tuple[str, str], ...] | None = None


class _VersionCache:
    """Parset struktur og foldet tekst pr. dokumentversion.

    Nøglen er ``(version_id, content_hash)`` — ikke selve teksten. Med
    teksten som nøgle ville hvert opslag hashe og sammenligne op til
    megabytes, og cachen ville holde en ekstra kopi af hver tekst. En
    version er uforanderlig, så id og indholdshash er nok.

    Cachen er afgrænset både i antal og i samlet tekststørrelse og smider
    de mindst nyligt brugte versioner ud først.
    """

    def __init__(self, max_entries: int, budget_chars: int) -> None:
        self._max_entries = max_entries
        self._budget = budget_chars
        self._entries: OrderedDict[tuple[int, str], _CachedVersion] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, version: DocumentVersion) -> _CachedVersion:
        key = (int(version.id or 0), version.content_hash or "")
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry
        content = version.content or ""
        entry = _CachedVersion(structure=parse_legal_structure(content), size=len(content))
        with self._lock:
            if key not in self._entries:
                self._entries[key] = entry
                self._size += entry.size
                self._evict()
            return self._entries.get(key, entry)

    def folded(self, version: DocumentVersion) -> tuple[tuple[str, str], ...]:
        """Foldet tekst pr. paragraf. Foldningen var den tungeste del af en
        søgning og gøres nu én gang pr. version."""
        entry = self.get(version)
        if entry.folded is None:
            entry.folded = tuple(
                (
                    fold(paragraph.text),
                    fold(" ".join(filter(None, [paragraph.chapter_title, paragraph.heading]))),
                )
                for paragraph in entry.structure.paragraphs
            )
        return entry.folded

    def _evict(self) -> None:
        while self._entries and (
            len(self._entries) > self._max_entries or self._size > self._budget
        ):
            if len(self._entries) == 1:
                break  # den nyeste beholdes, selv hvis den alene er over budget
            _, oldest = self._entries.popitem(last=False)
            self._size -= oldest.size

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def size(self) -> int:
        return self._size


_cache = _VersionCache(_STRUCTURE_CACHE_SIZE, _STRUCTURE_CACHE_BUDGET_CHARS)


def clear_structure_cache() -> None:
    """Rydder cachen. Bruges i test."""
    _cache.clear()


def structure_for_version(version: DocumentVersion | None) -> LegalDocumentStructure | None:
    if version is None or not (version.content or "").strip():
        return None
    return _cache.get(version).structure


def _score_paragraph(folded_text: str, folded_context: str, terms: list[str]) -> tuple[float, int]:
    """(score, antal forskellige termer). Se modulets docstring."""
    distinct = 0
    occurrences = 0
    context_hits = 0
    for term in terms:
        count = folded_text.count(term)
        if count:
            distinct += 1
            occurrences += count
        if folded_context and term in folded_context:
            context_hits += 1
    if not distinct and not context_hits:
        return 0.0, 0
    return float(distinct * 10 + occurrences + context_hits * 5), distinct


def locate_paragraphs(
    session: Session,
    documents: list[Document],
    terms: list[str],
    *,
    per_document: int = 3,
    snippet_length: int = 320,
) -> dict[int, list[ParagraphHit]]:
    """De bedst matchende paragraffer pr. dokument.

    Uden søgeord returneres dokumentets *første* paragraf: den er
    stadig en bedre indgang end en vilkårlig tekststump, og den er hvad
    en gennemsynsliste skal vise.
    """
    folded_terms = [t for t in {fold(term).strip() for term in terms} if t]
    results: dict[int, list[ParagraphHit]] = {}

    for document in documents:
        version = document.current_version
        if version is None and document.current_version_id:
            version = session.get(DocumentVersion, document.current_version_id)
        structure = structure_for_version(version)
        if structure is None or not structure.has_paragraphs:
            continue

        title = document.display_title or document.title

        # Uden søgeord er svaret altid dokumentets første paragraffer. Det
        # kræver hverken foldning eller scoring af resten af teksten — og
        # på en gennemsynsliste med store, sammenskrevne love var netop den
        # foldning den dyreste del af sidevisningen.
        if not folded_terms:
            results[document.id] = [
                ParagraphHit(
                    document_id=document.id,
                    paragraph_id=paragraph.paragraph_id,
                    chapter_no=paragraph.chapter_no,
                    chapter_title=paragraph.chapter_title,
                    section_title=paragraph.section_title or paragraph.heading,
                    legal_path=paragraph.legal_path,
                    full_citation=paragraph.citation(title),
                    snippet=(
                        paragraph.text[:snippet_length]
                        + ("…" if len(paragraph.text) > snippet_length else "")
                    ),
                    score=0.0,
                    char_start=paragraph.char_start,
                    char_end=paragraph.char_end,
                )
                for paragraph in structure.paragraphs[:per_document]
            ]
            continue

        # Først scores alle paragraffer på den cachede foldede tekst; uddrag
        # dannes kun for de få, der vælges. En bred term rammer hundredvis
        # af paragraffer i en stor lov, og et uddrag pr. træf var spild.
        scored: list[tuple[float, int, int]] = []
        folded_index = _cache.folded(version)
        for order, (folded_body, folded_context) in enumerate(folded_index):
            score, _distinct = _score_paragraph(folded_body, folded_context, folded_terms)
            if score > 0:
                scored.append((score, -order, order))

        if not scored:
            continue
        scored.sort(key=lambda row: (-row[0], -row[1]))
        hits = []
        for score, _, order in scored[:per_document]:
            paragraph = structure.paragraphs[order]
            hits.append(
                ParagraphHit(
                    document_id=document.id,
                    paragraph_id=paragraph.paragraph_id,
                    chapter_no=paragraph.chapter_no,
                    chapter_title=paragraph.chapter_title,
                    section_title=paragraph.section_title or paragraph.heading,
                    legal_path=paragraph.legal_path,
                    full_citation=paragraph.citation(title),
                    snippet=make_snippet(paragraph.text, terms, max_length=snippet_length),
                    score=score,
                    char_start=paragraph.char_start,
                    char_end=paragraph.char_end,
                )
            )
        results[document.id] = hits

    return results

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

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import Session

from app.core.text import fold, make_snippet
from app.models import Document, DocumentVersion
from app.services.legal.structure import LegalDocumentStructure, parse_legal_structure

__all__ = ["ParagraphHit", "locate_paragraphs", "structure_for_version", "clear_structure_cache"]

#: Antal versioner der holdes parset i hukommelsen. En parset bekendtgørelse
#: fylder nogle få hundrede kilobyte; 128 er rigeligt til en travl side og
#: langt under, hvad containeren har.
_STRUCTURE_CACHE_SIZE = 128


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


@lru_cache(maxsize=_STRUCTURE_CACHE_SIZE)
def _parse_cached(version_id: int, content: str) -> LegalDocumentStructure:
    """Parsing cachet pr. version.

    ``content`` indgår i nøglen, selv om ``version_id`` alene ville være
    entydigt. Det gør funktionen korrekt også i test, hvor to databaser
    kan genbruge de samme id'er.
    """
    return parse_legal_structure(content)


def clear_structure_cache() -> None:
    """Rydder cachen. Bruges i test."""
    _parse_cached.cache_clear()


def structure_for_version(version: DocumentVersion | None) -> LegalDocumentStructure | None:
    if version is None or not (version.content or "").strip():
        return None
    return _parse_cached(int(version.id or 0), version.content)


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
        scored: list[tuple[float, int, ParagraphHit]] = []

        for order, paragraph in enumerate(structure.paragraphs):
            folded_body = fold(paragraph.text)
            folded_context = fold(
                " ".join(filter(None, [paragraph.chapter_title, paragraph.heading]))
            )
            if folded_terms:
                score, distinct = _score_paragraph(folded_body, folded_context, folded_terms)
                if score <= 0:
                    continue
            else:
                # Ingen søgeord: første paragraf først.
                score, distinct = float(len(structure.paragraphs) - order), 0

            scored.append(
                (
                    score,
                    -order,
                    ParagraphHit(
                        document_id=document.id,
                        paragraph_id=paragraph.paragraph_id,
                        chapter_no=paragraph.chapter_no,
                        chapter_title=paragraph.chapter_title,
                        section_title=paragraph.section_title or paragraph.heading,
                        legal_path=paragraph.legal_path,
                        full_citation=paragraph.citation(title),
                        snippet=(
                            make_snippet(paragraph.text, terms, max_length=snippet_length)
                            if folded_terms
                            else paragraph.text[:snippet_length]
                            + ("…" if len(paragraph.text) > snippet_length else "")
                        ),
                        score=score,
                        char_start=paragraph.char_start,
                        char_end=paragraph.char_end,
                    ),
                )
            )

        if not scored:
            continue
        scored.sort(key=lambda row: (-row[0], -row[1]))
        results[document.id] = [hit for _, _, hit in scored[:per_document]]

    return results

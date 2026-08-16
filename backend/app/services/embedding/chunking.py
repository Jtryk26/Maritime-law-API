"""Opdeling af lovtekst i indekserbare enheder.

Enheden er paragraffen
======================
Tidligere skar denne modul teksten i vinduer på omtrent 1.200 tegn og
flyttede snittet hen til nærmeste paragrafgrænse, hvis der tilfældigvis
lå én i nærheden. Det gav et indeks, hvis enheder ikke svarede til noget,
en jurist kan henvise til.

Nu læses lovens struktur først (:mod:`app.services.legal.structure`), og
**én paragraf bliver ét stykke**. Stykkerne flisebelægger teksten: hvert
stykke går fra slutningen af det forrige til begyndelsen af det næste, så
kapiteloverskrifter, mellemoverskrifter og bilagslinjer altid hører til
et bestemt stykke og aldrig falder mellem to.

Rækkefølgen af hensyn
=====================
1. Én paragraf = ét stykke, med kapitel og eventuelt afsnit på.
2. Er en enkelt paragraf for lang til at kunne vektoriseres meningsfuldt,
   deles den ved ``Stk. N``-grænser — aldrig midt i en bestemmelse.
3. Er et enkelt stykke stadig for langt, klippes ved sætningsskift.
4. Findes der slet ingen paragraffer (bilag, tabeller, vejledninger),
   falder modulet tilbage til afsnits- og sætningsopdeling. Stykkerne
   mærkes da ``unit_type="fragment"``, så det kan ses i indekset.

Præamblen
=========
Kundgørelsesformlen ("I medfør af § 1 i lov om ... fastsættes:") gemmes
som sit eget stykke med ``unit_type="preamble"``. Den skal bevares —
den er dokumentets hjemmel — men den er ikke en regel, og den skal ikke
konkurrere med paragrafferne om at være det bedste hit.

Om overlap
==========
Vinduesopdelingen havde overlap, fordi en bestemmelse kunne ligge hen
over et vilkårligt snit. Med strukturelle snit findes det problem ikke:
grænserne er lovens egne. ``overlap_chars`` bruges derfor kun i den
ustrukturerede nødsti, hvor snittene stadig er vilkårlige.

Kontekstpræfiks
===============
Hvert stykke vektoriseres med dokumentets titel og sin lovadresse foran.
Et stykke der blot siger "Reglerne i stk. 1 gælder ikke for fartøjer
under 15 meter" er meningsløst uden at vide hvilken bekendtgørelse det
står i. Præfikset gemmes ikke i ``content``, så uddraget i brugerfladen
er den rigtige lovtekst.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import normalize_whitespace, strip_html
from app.services.legal.structure import (
    LegalDocumentStructure,
    LegalParagraph,
    normalize_legal_text,
    parse_legal_structure,
)

__all__ = [
    "TextChunk",
    "chunk_document",
    "ChunkingConfig",
    "normalize_whitespace_preserving_breaks",
]

#: Enhedstyper. Gemmes på `DocumentChunk.unit_type`.
UNIT_PREAMBLE = "preamble"
UNIT_PARAGRAPH = "paragraph"
UNIT_FRAGMENT = "fragment"

_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆØÅ§])")


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Parametre for opdelingen. Kommer fra Settings, kan sættes i test.

    ``target_chars`` og ``max_chars`` er ikke længere mål for hvor store
    stykkerne *skal* være — en paragraf holdes samlet, uanset om den er
    på 200 eller 1.800 tegn. De er lofter for, hvornår en paragraf er så
    lang, at den må deles.
    """

    target_chars: int = 1200
    max_chars: int = 2000
    #: Kun brugt i den ustrukturerede nødsti. Se modulets docstring.
    overlap_chars: int = 150
    min_chars: int = 120
    max_per_document: int = 400


@dataclass(slots=True)
class TextChunk:
    """Én indekserbar enhed med sin plads i loven."""

    index: int
    content: str
    char_start: int
    char_end: int
    unit_type: str = UNIT_PARAGRAPH
    chapter_no: str | None = None
    chapter_title: str | None = None
    section_no: str | None = None
    section_title: str | None = None
    paragraph_id: str | None = None
    paragraph_sort_key: str | None = None
    #: Sat når en lang paragraf måtte deles: 1, 2, 3 ...
    part: int | None = None

    # -- Afledte visningsfelter --------------------------------------------

    @property
    def chapter(self) -> str | None:
        """"Kapitel 3" — uden titel, til kompakt visning."""
        return f"Kapitel {self.chapter_no}" if self.chapter_no else None

    @property
    def paragraph(self) -> str | None:
        return self.paragraph_id

    @property
    def chapter_label(self) -> str | None:
        if not self.chapter_no:
            return None
        base = f"Kapitel {self.chapter_no}"
        return f"{base} — {self.chapter_title}" if self.chapter_title else base

    @property
    def legal_path(self) -> str:
        """Stykkets adresse i loven: "Kapitel 3 · § 12".

        Kapitel OG paragraf, ikke bare den nærmeste af dem. "§ 12" alene
        siger intet om, hvad reglen handler om, og to forskellige loves
        § 12 ser ens ud uden kapitlet.
        """
        parts = [p for p in (self.chapter, self.paragraph_id) if p]
        path = " · ".join(parts)
        if self.part and self.part > 1 and path:
            path = f"{path} (del {self.part})"
        return path

    @property
    def heading(self) -> str | None:
        """Nærmeste meningsfulde overskrift — til kompakt visning."""
        if self.unit_type == UNIT_PREAMBLE:
            return "Præambel"
        return self.paragraph_id or self.chapter or self.section_title or None

    def citation(self, document_title: str | None = None) -> str:
        head = normalize_whitespace(document_title or "")
        if self.unit_type == UNIT_PREAMBLE:
            return f"{head} (præambel)".strip()
        tail = self.paragraph_id or self.chapter or ""
        if self.paragraph_id and self.chapter_no:
            tail = f"{self.paragraph_id}, kapitel {self.chapter_no}"
        return f"{head} {tail}".strip()

    def embedding_text(self, title: str | None = None, document_number: str | None = None) -> str:
        """Teksten der faktisk sendes til modellen — med kontekst foran.

        Uden præfikset er et stykke som "Stk. 2. Reglerne i stk. 1 gælder
        ikke for lastskibe under 500 BT" ren støj i vektorrummet: det
        siger hverken hvilken lov, hvilket emne eller hvilken regel der
        undtages. Modellen har intet andet sted at få det fra.
        """
        head = title or ""
        if document_number and head:
            head = f"{head} (nr. {document_number})"
        context = " · ".join(
            part for part in (self.chapter_label, self.section_title, self.paragraph_id) if part
        )
        parts = [p for p in (head, context) if p]
        prefix = " — ".join(parts)
        return f"{prefix}\n{self.content}" if prefix else self.content

    def to_metadata(self, document_title: str | None = None) -> dict:
        """Feltsættet der følger med ind i `document_chunks`."""
        return {
            "unit_type": self.unit_type,
            "chapter_no": self.chapter_no,
            "chapter_title": self.chapter_title,
            "section_no": self.section_no,
            "section_title": self.section_title,
            "paragraph_id": self.paragraph_id,
            "paragraph_sort_key": self.paragraph_sort_key,
            "full_citation": self.citation(document_title),
        }


def normalize_whitespace_preserving_breaks(text: str) -> str:
    """Som `normalize_whitespace`, men afsnitsskift bevares.

    Opdelingen har brug for linjeskiftene: de er det eneste sted §-, Stk.-
    og Kapitel-mønstrene kan forankre sig.
    """
    return normalize_legal_text(text) if text else ""


# ---------------------------------------------------------------------------
# Opdeling
# ---------------------------------------------------------------------------


def _emit(
    chunks: list[TextChunk],
    text: str,
    start: int,
    end: int,
    *,
    unit_type: str,
    paragraph: LegalParagraph | None = None,
    part: int | None = None,
) -> None:
    """Tilføjer ét stykke, hvis udsnittet indeholder tekst."""
    content = text[start:end].strip()
    if not content:
        return
    chunks.append(
        TextChunk(
            index=len(chunks),
            content=content,
            char_start=start,
            char_end=end,
            unit_type=unit_type,
            chapter_no=paragraph.chapter_no if paragraph else None,
            chapter_title=paragraph.chapter_title if paragraph else None,
            section_no=paragraph.section_no if paragraph else None,
            section_title=(paragraph.section_title or paragraph.heading) if paragraph else None,
            paragraph_id=paragraph.paragraph_id if paragraph else None,
            paragraph_sort_key=paragraph.sort_key if paragraph else None,
            part=part,
        )
    )


def _split_points(text: str, start: int, end: int, paragraph: LegalParagraph) -> list[int]:
    """Grænser en for lang paragraf må deles ved.

    Stykkegrænser (``Stk. N``) først; findes der ingen, sætningsskift.
    Aldrig midt i en sætning, medmindre der ikke findes en eneste grænse.
    """
    points = [
        sub.char_start
        for sub in paragraph.subsections
        if start < sub.char_start < end
    ]
    if points:
        return points

    return [start + m.start() for m in _SENTENCE_END_RE.finditer(text[start:end]) if m.start() > 0]


def _chunk_paragraph(
    chunks: list[TextChunk],
    text: str,
    start: int,
    end: int,
    paragraph: LegalParagraph,
    config: ChunkingConfig,
) -> None:
    """Én paragraf ind i indekset — delt, hvis den er for lang."""
    if end - start <= config.max_chars:
        _emit(chunks, text, start, end, unit_type=UNIT_PARAGRAPH, paragraph=paragraph)
        return

    boundaries = _split_points(text, start, end, paragraph)
    if not boundaries:
        # Ingen grænser overhovedet — en enkelt meget lang sætning eller
        # en tabel. Hårdt snit ved mellemrum, så intet ord deles.
        cursor = start
        part = 1
        while cursor < end and len(chunks) < config.max_per_document:
            stop = min(cursor + config.max_chars, end)
            if stop < end:
                space = text.rfind(" ", cursor + config.min_chars, stop)
                if space > cursor:
                    stop = space
            _emit(chunks, text, cursor, stop, unit_type=UNIT_PARAGRAPH, paragraph=paragraph, part=part)
            cursor = stop
            part += 1
        return

    # Saml stykkegrænser op, så hvert delstykke bliver så stort som det
    # må — færre og mere sammenhængende dele end ét pr. Stk.
    cuts: list[int] = []
    anchor = start
    for point in boundaries:
        if point - anchor >= config.target_chars:
            cuts.append(point)
            anchor = point
    edges = [start, *cuts, end]

    for part, (piece_start, piece_end) in enumerate(
        zip(edges[:-1], edges[1:], strict=True), start=1
    ):
        if len(chunks) >= config.max_per_document:
            return
        _emit(
            chunks,
            text,
            piece_start,
            piece_end,
            unit_type=UNIT_PARAGRAPH,
            paragraph=paragraph,
            part=part if len(edges) > 2 else None,
        )


def _chunk_unstructured(
    chunks: list[TextChunk],
    text: str,
    start: int,
    end: int,
    config: ChunkingConfig,
) -> None:
    """Nødsti for tekst uden paragraffer: bilag, tabeller, vejledninger.

    Her er snittene stadig vilkårlige, og derfor er det også her,
    ``overlap_chars`` giver mening: en bestemmelse kan ligge hen over et
    snit, som lovteksten ikke selv har sat.
    """
    position = start
    while position < end and len(chunks) < config.max_per_document:
        remaining = end - position
        if remaining <= config.max_chars:
            stop = end
        else:
            window_end = min(position + config.max_chars, end)
            target = min(position + config.target_chars, end)
            stop = _best_break(text, position, target, window_end, config)

        _emit(chunks, text, position, stop, unit_type=UNIT_FRAGMENT)

        if stop >= end:
            break
        position = max(stop - config.overlap_chars, position + 1) if config.overlap_chars else stop


def _best_break(text: str, start: int, target: int, hard_end: int, config: ChunkingConfig) -> int:
    """Pæneste snit mellem `start` og `hard_end` i ustruktureret tekst."""
    window = text[start:hard_end]
    relative_target = target - start

    def _pick(positions: list[int]) -> int | None:
        candidates = [p for p in positions if p >= config.min_chars]
        if not candidates:
            return None
        before = [c for c in candidates if c <= relative_target]
        return before[-1] if before else candidates[0]

    for pattern in (_PARAGRAPH_BREAK_RE, _SENTENCE_END_RE):
        cut = _pick([m.start() for m in pattern.finditer(window)])
        if cut is not None:
            return start + cut

    space = window.rfind(" ", config.min_chars, relative_target)
    if space > 0:
        return start + space
    return hard_end


def chunk_document(
    content: str,
    config: ChunkingConfig | None = None,
    *,
    structure: LegalDocumentStructure | None = None,
) -> list[TextChunk]:
    """Deler et dokuments tekst i indekserbare enheder.

    Tom tekst giver en tom liste. Kaster aldrig: en tekst der ikke kan
    struktureres, opdeles i afsnit.
    """
    cfg = config or ChunkingConfig()
    text = normalize_legal_text(content or "")
    if not text.strip():
        return []

    parsed = structure if structure is not None and structure.text == text else parse_legal_structure(text)
    chunks: list[TextChunk] = []

    if not parsed.has_paragraphs:
        _chunk_unstructured(chunks, text, 0, len(text), cfg)
        return chunks

    paragraphs = parsed.paragraphs

    # --- Præambel og alt før den første paragraf --------------------------
    # Er der en kundgørelsesformel, får den sit eget stykke. Kapitel- og
    # mellemoverskrifter mellem præamblen og § 1 hører til § 1's stykke,
    # så de ikke står alene som søgeresultat.
    head_end = parsed.preamble_end if parsed.preamble else 0
    if parsed.preamble:
        _emit(chunks, text, 0, head_end, unit_type=UNIT_PREAMBLE)

    # --- Paragrafferne, flisebelagt ---------------------------------------
    # Overskriftsblokken FORAN en paragraf hører til den paragraf, ikke til
    # den forrige: "Kapitel 2 · Brandsektionering" indleder § 2 og skal stå
    # i dens stykke, så både mennesket og modellen ser emnet sammen med
    # reglen.
    cursor = head_end
    for index, paragraph in enumerate(paragraphs):
        if len(chunks) >= cfg.max_per_document:
            break
        start = min(cursor, paragraph.char_start)
        end = len(text) if index + 1 == len(paragraphs) else paragraph.char_end
        _chunk_paragraph(chunks, text, start, end, paragraph, cfg)
        cursor = end

    # Halen efter sidste paragraf er allerede med: dens `end` er len(text).
    return chunks

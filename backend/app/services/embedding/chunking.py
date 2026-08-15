"""Opdeling af lovtekst i chunks.

En bekendtgørelse kan fylde hundredtusind tegn. Én vektor for hele
teksten ville blive et gennemsnit af alt og ligne alting lidt. Derfor
vektoriseres teksten stykkevis, og et dokuments lighed med en søgning er
ligheden med dets bedste stykke.

Snitfladen følger lovteksten
============================
Grænserne søges i denne rækkefølge:

1. ``Kapitel N`` og ``§ N`` — en paragraf er den enhed en jurist
   henviser til, og det er derfor den enhed et søgeresultat helst skal
   svare til.
2. ``Stk. N`` — næste niveau, når en enkelt paragraf er for lang.
3. Afsnitsskift, derefter sætningsskift.
4. Hårdt snit ved ``chunk_max_chars``, hvis intet andet findes (tabeller
   og bilag har ofte ingen brugbare grænser).

Overlap
=======
Nabo-chunks deler de sidste ``chunk_overlap_chars`` tegn. En bestemmelse
der ligger hen over en grænse går dermed ikke tabt i begge stykker.

Kontekstpræfiks
===============
Hvert chunk vektoriseres med dokumentets titel og den nærmeste
overskrift foran selve teksten. Et stykke der blot siger "Reglerne i
stk. 1 gælder ikke for fartøjer under 15 meter" er meningsløst uden at
vide hvilken bekendtgørelse det står i — og modellen har intet andet
sted at få det fra. Selve `content` gemmes uden præfikset, så uddraget i
brugerfladen er den rigtige lovtekst.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import normalize_whitespace, strip_html

__all__ = ["TextChunk", "chunk_document", "ChunkingConfig"]


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Chunk-parametre. Kommer fra Settings, men kan sættes direkte i test."""

    target_chars: int = 1200
    max_chars: int = 2000
    overlap_chars: int = 150
    min_chars: int = 120
    max_per_document: int = 400


@dataclass(slots=True)
class TextChunk:
    """Ét stykke lovtekst med sin placering i den oprindelige tekst."""

    index: int
    content: str
    char_start: int
    char_end: int
    #: Nærmeste overskrift over stykket ("§ 12", "Kapitel 3"), hvis fundet.
    heading: str | None = None

    def embedding_text(self, title: str | None = None) -> str:
        """Teksten der faktisk sendes til modellen — med kontekst foran."""
        parts = [p for p in (title, self.heading) if p]
        prefix = " — ".join(parts)
        return f"{prefix}\n{self.content}" if prefix else self.content


# "§ 12", "§ 12 a", "§§ 3-5" i starten af en linje eller efter punktum.
_PARAGRAPH_RE = re.compile(r"(?m)^\s*(§+\s*\d+\s*[a-zA-Z]?)")
_CHAPTER_RE = re.compile(r"(?mi)^\s*(kapitel\s+[0-9ivxlc]+)")
_SUBSECTION_RE = re.compile(r"(?m)^\s*(Stk\.\s*\d+)")
_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆØÅ§])")
#: Til overskriftssporing i den rensede tekst.
_HEADING_SCAN_RE = re.compile(
    r"(?mi)^\s*(kapitel\s+[0-9ivxlc]+|§+\s*\d+\s*[a-zA-Z]?)"
)


def _cut_points(text: str) -> list[int]:
    """Alle mulige snitpunkter, stærkeste først i prioritet.

    Returnerer positioner sorteret stigende. Prioriteringen ligger i
    hvilken liste positionen kom fra — den håndteres af :func:`_best_cut`.
    """
    return sorted(
        {m.start() for m in _CHAPTER_RE.finditer(text)}
        | {m.start() for m in _PARAGRAPH_RE.finditer(text)}
    )


def _best_cut(text: str, start: int, target_end: int, hard_end: int) -> int:
    """Finder det pæneste snit mellem `start` og `hard_end`.

    Vi foretrækker et snit tæt på `target_end`, men vil hellere flytte os
    et stykke for at ramme en paragrafgrænse end at skære midt i en
    bestemmelse.
    """
    window = text[start:hard_end]
    if not window:
        return hard_end

    relative_target = target_end - start

    def _pick(matches: list[int]) -> int | None:
        """Snittet tættest på target, dog aldrig så tidligt at stykket
        bliver ubrugeligt kort."""
        candidates = [m for m in matches if m > 0]
        if not candidates:
            return None
        # Foretræk det sidste snit som ikke overskrider målet; ellers det
        # første efter målet.
        before = [c for c in candidates if c <= relative_target]
        if before:
            return before[-1]
        return candidates[0]

    for pattern in (_CHAPTER_RE, _PARAGRAPH_RE, _SUBSECTION_RE):
        cut = _pick([m.start() for m in pattern.finditer(window)])
        if cut is not None:
            return start + cut

    cut = _pick([m.start() for m in _PARAGRAPH_BREAK_RE.finditer(window)])
    if cut is not None:
        return start + cut

    cut = _pick([m.start() for m in _SENTENCE_END_RE.finditer(window)])
    if cut is not None:
        return start + cut

    # Sidste udvej: hårdt snit. Prøv dog at ramme et mellemrum.
    space = window.rfind(" ", 0, relative_target)
    if space > 0:
        return start + space
    return hard_end


def _heading_at(text: str, position: int) -> str | None:
    """Nærmeste overskrift på eller før `position`.

    Hele teksten skannes, og matches filtreres på startposition. En
    afkortet søgning (`finditer(text, 0, position + 1)`) ville overse en
    overskrift, der begynder præcis dér hvor stykket begynder — netop det
    almindeligste tilfælde, da snittene lægges på overskrifterne.
    """
    heading: str | None = None
    for match in _HEADING_SCAN_RE.finditer(text):
        if match.start() > position:
            break
        heading = normalize_whitespace(match.group(1))
    return heading


def chunk_document(content: str, config: ChunkingConfig | None = None) -> list[TextChunk]:
    """Deler et dokuments tekst op. Tom tekst giver en tom liste."""
    cfg = config or ChunkingConfig()
    text = normalize_whitespace_preserving_breaks(strip_html(content or ""))
    if not text.strip():
        return []

    chunks: list[TextChunk] = []
    position = 0
    length = len(text)
    # Et for kort stykke uden noget foran sig — typisk en kapiteloverskrift
    # på egen linje — bæres med over i det NÆSTE stykke. Alternativet var at
    # lade "Kapitel 2" stå som selvstændigt søgeresultat, og en overskrift
    # alene besvarer ingen søgning.
    carry: str | None = None
    carry_start = 0

    while position < length and len(chunks) < cfg.max_per_document:
        remaining = length - position
        if remaining <= cfg.max_chars:
            end = length
        else:
            end = _best_cut(
                text,
                position,
                min(position + cfg.target_chars, length),
                min(position + cfg.max_chars, length),
            )
            if end <= position:  # sikkerhedsnet mod uendelig løkke
                end = min(position + cfg.max_chars, length)

        piece = text[position:end]
        stripped = piece.strip()

        if stripped:
            if carry:
                stripped = f"{carry}\n{stripped}".strip()

            start = carry_start if carry else position
            carry = None

            if len(stripped) < cfg.min_chars and chunks:
                # For kort til at stå alene: lægges til det forrige stykke.
                previous = chunks[-1]
                previous.content = f"{previous.content}\n{stripped}".strip()
                previous.char_end = end
            elif len(stripped) < cfg.min_chars and end < length:
                # For kort, og intet forrige stykke at lægge det til.
                # Bæres videre til det næste.
                carry = stripped
                carry_start = start
            else:
                chunks.append(
                    TextChunk(
                        index=len(chunks),
                        content=stripped,
                        char_start=start,
                        char_end=end,
                        heading=_heading_at(text, start),
                    )
                )

        if end >= length:
            if carry:
                # Sidste stykke var for kort og har intet at hænge på.
                # Bedre et kort stykke end tabt lovtekst.
                chunks.append(
                    TextChunk(
                        index=len(chunks),
                        content=carry,
                        char_start=carry_start,
                        char_end=length,
                        heading=_heading_at(text, carry_start),
                    )
                )
                carry = None
            break

        # Næste stykke starter lidt inde i det forrige.
        next_position = end - cfg.overlap_chars if cfg.overlap_chars > 0 else end
        position = max(next_position, position + 1)

    return chunks


def normalize_whitespace_preserving_breaks(text: str) -> str:
    """Som `normalize_whitespace`, men afsnitsskift bevares.

    Chunkeren har brug for linjeskiftene: de er det eneste sted §- og
    Stk.-mønstrene kan forankre sig. `normalize_whitespace` klapper alt
    sammen til enkelte mellemrum og er derfor forkert her.
    """
    if not text:
        return ""
    # Normalisér linjeskift, fjern hængende mellemrum, klap tre eller
    # flere tomme linjer sammen til én.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

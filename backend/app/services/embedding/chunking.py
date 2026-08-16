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
    #: Nærmeste overskrift over stykket ("§ 12" eller "Kapitel 3").
    #: Bevaret for visning i brugerfladen.
    heading: str | None = None
    #: Kapitlet stykket hører under, hvis et sådant findes.
    chapter: str | None = None
    #: Paragraffen stykket hører under.
    paragraph: str | None = None

    @property
    def legal_path(self) -> str:
        """Stykkets adresse i loven: "Kapitel 3 · § 12".

        Kapitel OG paragraf, ikke bare den nærmeste af dem. Forskellen
        betyder noget: "§ 12" alene siger intet om, hvad reglen handler
        om, mens "Kapitel 3 Skibets drift · § 12" placerer den. To
        forskellige loves § 12 ser desuden ens ud uden kapitlet.
        """
        return " · ".join(p for p in (self.chapter, self.paragraph) if p)

    def embedding_text(self, title: str | None = None, document_number: str | None = None) -> str:
        """Teksten der faktisk sendes til modellen — med kontekst foran.

        Uden præfikset er et stykke som "Stk. 2. Reglerne i stk. 1 gælder
        ikke for lastskibe under 500 BT" ren støj i vektorrummet: det
        siger hverken hvilken lov, hvilket emne eller hvilken regel der
        undtages. Modellen har intet andet sted at få det fra.

        Præfikset gemmes IKKE i `content`, så uddraget i brugerfladen
        forbliver den rigtige lovtekst.
        """
        head = title or ""
        if document_number and head:
            head = f"{head} (nr. {document_number})"
        parts = [p for p in (head, self.legal_path) if p]
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


def _context_at(text: str, position: int) -> tuple[str | None, str | None, str | None]:
    """(kapitel, paragraf, nærmeste overskrift) på eller før `position`.

    Kapitel og paragraf spores hver for sig frem for at tage "den sidste
    overskrift". Et stykke inde i § 12 under kapitel 3 skal bære BEGGE
    dele: kapitlet siger hvad reglen handler om, paragraffen siger hvor
    den står.

    Hele teksten skannes, og matches filtreres på startposition. En
    afkortet søgning (`finditer(text, 0, position + 1)`) ville overse en
    overskrift, der begynder præcis dér hvor stykket begynder — netop det
    almindeligste tilfælde, da snittene lægges på overskrifterne.
    """
    chapter: str | None = None
    paragraph: str | None = None
    nearest: str | None = None

    for match in _HEADING_SCAN_RE.finditer(text):
        if match.start() > position:
            break
        value = normalize_whitespace(match.group(1))
        nearest = value
        if value.lower().startswith("kapitel"):
            chapter = value
            # Et nyt kapitel nulstiller paragraffen: § 1 i kapitel 4 er
            # ikke en fortsættelse af § 9 i kapitel 3.
            paragraph = None
        else:
            paragraph = value

    return chapter, paragraph, nearest


def _chunk_context(text: str, position: int) -> dict[str, str | None]:
    """Felterne der beskriver stykkets plads i loven.

    Grænsen for hvor langt der skannes er slutningen af stykkets FØRSTE
    LINJE, ikke stykkets startposition. Et stykke der begynder med
    "§ 12." ville ellers arve den forrige paragraf: overskriftsmønstret
    er forankret i linjestarten, så dets match begynder et tegn eller to
    før selve paragraftegnet, og et snit lagt præcis på paragraffen
    faldt derfor uden for. Resultatet var stykker mærket "§ 11", som
    handlede om § 12.

    For et stykke midt inde i en paragraf indeholder første linje ingen
    overskrift, og den nærmeste foregående bruges — hvilket er det
    rigtige.
    """
    newline = text.find("\n", position)
    boundary = newline if newline != -1 else len(text)
    chapter, paragraph, nearest = _context_at(text, boundary)
    return {"chapter": chapter, "paragraph": paragraph, "heading": nearest}


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
    #: Startpunkt for en for kort forløber, der skal med i næste stykke —
    #: typisk en kapiteloverskrift på egen linje. Et indeks, ikke en
    #: streng, netop så stykket forbliver ét udsnit af kildeteksten.
    carry: int | None = None

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

        # Et stykke er ALTID et sammenhængende udsnit af teksten:
        # `text[start:end]`. Tidligere blev korte stykker føjet til det
        # forrige ved at sætte strengene sammen — men med overlap
        # begynder det korte stykke inde i det forrige, så teksten blev
        # skrevet to gange og kunne ikke længere findes i kilden. Ved at
        # skære påny fra kildeteksten kan det ikke ske.
        stripped = text[position:end].strip()

        if stripped:
            if carry is not None:
                start = carry
                carry = None
            else:
                start = position

            merged = text[start:end].strip()

            if len(merged) < cfg.min_chars and chunks:
                # For kort til at stå alene: det forrige stykke udvides,
                # så teksten forbliver ét sammenhængende udsnit.
                previous = chunks[-1]
                previous.char_end = end
                previous.content = text[previous.char_start:end].strip()
            elif len(merged) < cfg.min_chars and end < length:
                # For kort, og intet forrige stykke at lægge det til.
                # Startpunktet bæres videre til næste omgang.
                carry = start
            else:
                chunks.append(
                    TextChunk(
                        index=len(chunks),
                        content=merged,
                        char_start=start,
                        char_end=end,
                        **_chunk_context(text, start),
                    )
                )

        if end >= length:
            if carry is not None:
                # Halen var for kort og har intet at hænge på. Bedre et
                # kort stykke end tabt lovtekst.
                tail = text[carry:length].strip()
                if tail:
                    chunks.append(
                        TextChunk(
                            index=len(chunks),
                            content=tail,
                            char_start=carry,
                            char_end=length,
                            **_chunk_context(text, carry),
                        )
                    )
                carry = None
            break

        # Næste stykke starter lidt inde i det forrige, så en bestemmelse
        # hen over en grænse ikke går tabt begge steder.
        #
        # Undtagen når noget bæres fremad: `carry` indeholder ALLEREDE
        # teksten frem til `end`, og et overlap oven i den ville skrive
        # den samme passage to gange i samme stykke — og skære den midt
        # over på købet.
        if carry is not None or cfg.overlap_chars <= 0:
            position = max(end, position + 1)
        else:
            position = max(end - cfg.overlap_chars, position + 1)

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

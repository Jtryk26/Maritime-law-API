"""Strukturel parsing af dansk lovtekst.

Hvorfor ikke token-chunks
=========================
Den tidligere opdeling skar teksten i stykker på omtrent 1.200 tegn og
flyttede kun snittet hen til nærmeste paragrafgrænse, når der tilfældigvis
lå én i nærheden. Resultatet var et indeks, hvis enheder ikke svarede til
noget en jurist kan henvise til: et stykke kunne indeholde halvanden
paragraf, og en kort paragraf kunne blive slugt af sin nabo.

Denne parser vender det om. Først findes lovens **struktur**:

.. code-block:: text

    dokument
    ├── præambel            ("I medfør af § 1 i lov om ... fastsættes:")
    ├── Afsnit I            (valgfrit)
    │   └── Kapitel 1  Anvendelsesområde
    │       ├── § 1
    │       │   ├── Stk. 2
    │       │   └── Stk. 3
    │       └── § 2
    └── Kapitel 2 ...

Derefter er **paragraffen** enheden. Kun hvis en enkelt paragraf er så
lang, at den ikke kan vektoriseres i ét stykke, deles den — og da ved
stykkegrænser (``Stk. N``), aldrig midt i en sætning.

Præamblen gemmes for sig
========================
Kundgørelsesformlen er hverken en regel eller et emne. Den fylder ofte
det første skærmbillede i en dokumentvisning og ville som selvstændig
retrieval-enhed matche enhver søgning på det ministerium, der udstedte
bekendtgørelsen. Den bevares derfor, men uden for paragrafrækken.

Robusthed
=========
Teksten kan være alt fra velformateret XML-udtræk til en enkelt lang
linje. Findes ingen paragraffer overhovedet (vejledninger, bilag,
tabeller), returneres en struktur uden paragraffer, og kaldere falder
tilbage til afsnitsopdeling. Parseren kaster aldrig på skæve input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.text import normalize_whitespace, strip_html

__all__ = [
    "LegalSubsection",
    "LegalParagraph",
    "LegalChapter",
    "LegalDocumentStructure",
    "parse_legal_structure",
    "paragraph_sort_key",
    "normalize_legal_text",
]


# --- Mønstre ---------------------------------------------------------------
# Alle er forankret i linjestart. Juridiske overskrifter står på egen linje
# i alt materiale vi har set; et mønster der også matchede midt i en
# sætning ville gøre enhver krydshenvisning ("jf. § 4") til en ny paragraf.

#: Nummeret på et kapitel eller afsnit: "3", "3 a", "IV".
#:
#: Bogstavet må KUN tælles med, når det står alene. Uden det negative
#: lookahead æder "\d+\s*[a-zA-Z]?" det første bogstav af en titel på
#: samme linje: "Kapitel 1 Anvendelsesområde" blev til nummer "1 A" med
#: titlen "nvendelsesområde". Det viste sig først, da kildens tekst kom
#: ind på én linje — fixturerne har nummer og titel på hver sin linje.
_LEVEL_NUMBER = r"\d+(?:\s*[a-zA-Z](?![a-zA-ZæøåÆØÅ]))?|[IVXLCDM]+"

#: "Kapitel 3", "Kapitel 3 a", "Kapitel IV" — med valgfri titel på samme linje.
_CHAPTER_RE = re.compile(
    rf"^\s*(?P<label>Kapitel)\s+(?P<number>{_LEVEL_NUMBER})\s*[.:]?\s*(?P<title>.*)$",
    re.IGNORECASE,
)
#: "Afsnit I", "Afsnit 2" — det niveau der ligger over kapitler i store love.
_PART_RE = re.compile(
    rf"^\s*(?P<label>Afsnit)\s+(?P<number>{_LEVEL_NUMBER})\s*[.:]?\s*(?P<title>.*)$",
    re.IGNORECASE,
)
#: "§ 1.", "§ 12 a.", "§ 3 b". Ét paragraftegn — "§§ 3-5" er en henvisning.
_PARAGRAPH_RE = re.compile(
    r"^\s*§\s*(?P<number>\d+)\s*(?P<letter>[a-zA-Z])?\s*\.?\s*(?P<rest>.*)$"
)
#: "Stk. 2." — stykke inde i en paragraf.
_SUBSECTION_RE = re.compile(r"^\s*Stk\.\s*(?P<number>\d+)\s*\.?\s*(?P<rest>.*)$")
#: Kundgørelsesformlen. Kendetegner præamblen i bekendtgørelser og anordninger.
_PROMULGATION_RE = re.compile(r"\bI\s+medf(ø|oe)r\s+af\b", re.IGNORECASE)

#: En unummereret mellemoverskrift: kort linje uden slutpunktum, som ikke
#: selv er en paragraf eller et stykke. "Anvendelsesområde", "Definitioner".
_HEADING_MAX_CHARS = 90


# --- Flad tekst ------------------------------------------------------------
# Nogle kilder leverer et helt dokument på én linje. Det sker, når XML'en
# ikke har markup pr. bestemmelse, og det skete i praksis for hele
# produktionssamlingen, da `xml_parser._element_text` klappede linjeskiftene
# sammen. Mønstrene ovenfor er forankret i linjestart og finder da intet.
#
# Derfor kan en åbner også genkendes MIDT i en linje — men kun under
# skrappe betingelser. En krydshenvisning som "jf. § 4" må under ingen
# omstændigheder blive til en ny bestemmelse: det ville flytte lovtekst
# over i en paragraf, den ikke hører til, og et søgeresultat ville pege på
# et sted, hvor reglen ikke står.

#: Efter en åbner følger lovtekst, altså et stort begyndelsesbogstav.
#:
#: Slutningen af segmentet tæller bevidst IKKE med. En bestemmelse uden
#: indhold er ikke en bestemmelse, og "… gælder dog ikke § 8." slutter
#: netop sådan. For en åbner i linjestart er kravet unødvendigt — dér er
#: der ingen tvivl — men midt i en linje er det et af de stærkeste værn.
_FOLLOWED_BY_TEXT = r"(?=\s+[A-ZÆØÅ«\"])"

#: Kanonisk åbnerform midt i tekst: "§ 12." — tallet SKAL følges af
#: punktum. En henvisning skrives "§ 12", "§ 12, stk. 2" eller "§§ 3-5".
_FLAT_PARAGRAPH_RE = re.compile(
    rf"§\s*(?P<number>\d+)\s*(?P<letter>[a-zA-Z](?![a-zA-ZæøåÆØÅ]))?\s*\.{_FOLLOWED_BY_TEXT}"
)
_FLAT_CHAPTER_RE = re.compile(
    rf"(?P<label>Kapitel)\s+(?P<number>{_LEVEL_NUMBER})\b{_FOLLOWED_BY_TEXT}",
    re.IGNORECASE,
)
_FLAT_SUBSECTION_RE = re.compile(
    rf"Stk\.\s*(?P<number>\d+)\s*\.{_FOLLOWED_BY_TEXT}"
)

#: ... men et punktum er ikke altid en sætningsslutning. Står et af disse
#: ord lige før punktummet, er "punktummet" en forkortelse, og det der
#: følger er en henvisning — ikke en ny bestemmelse.
#:
#:     "... ansvaret, jf. § 4."      -> henvisning
#:     "... jf. dog § 4, stk. 2."    -> henvisning
#:     "... er sødygtigt. § 5."      -> ny bestemmelse
_ABBREVIATION_BEFORE_RE = re.compile(
    r"(?:^|[\s,;(])(?:jf|nr|stk|pkt|litra|kap|bek|lbk|lov|art|jfr|bilag|afsn|"
    r"eks|ca|mv|m\.v|f\.eks)\.\s*$",
    re.IGNORECASE,
)

#: Et ord der aldrig kan stå umiddelbart før en ny bestemmelse. Fanger de
#: henvisninger, hvor der slet ikke er noget punktum: "i § 4", "efter § 7".
_REFERENCE_CUE_RE = re.compile(
    r"(?:^|\s)(?:i|efter|til|og|samt|eller|af|ved|se|om|jf|jfr|medfør|henhold|"
    r"omfattet|nævnt|følger|medmindre|dog|ikke|end|herunder|fra|under|"
    r"anvendelse|gælder|jf\.)\s*$",
    re.IGNORECASE,
)


def normalize_legal_text(content: str) -> str:
    """Kanonisk tekstform til strukturparsing.

    Linjeskiftene bevares — de er det eneste sted overskriftsmønstrene kan
    forankre sig — men mellemrum indeni en linje klappes sammen, og mere
    end én tom linje bliver til én.
    """
    if not content:
        return ""
    text = strip_html(content)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def paragraph_sort_key(number: str | int, letter: str | None = None) -> str:
    """Sorteringsnøgle så § 2 kommer før § 10, og § 12 før § 12 a.

    >>> paragraph_sort_key(2)
    '0002'
    >>> paragraph_sort_key(12, "a")
    '0012a'
    """
    try:
        numeric = int(str(number).strip())
    except (TypeError, ValueError):
        return f"9999{str(number or '').strip().lower()}"
    suffix = (letter or "").strip().lower()
    return f"{numeric:04d}{suffix}"


@dataclass(slots=True)
class LegalSubsection:
    """Ét stykke (``Stk. N``) inde i en paragraf."""

    number: int
    text: str
    char_start: int
    char_end: int

    @property
    def label(self) -> str:
        return f"Stk. {self.number}"


@dataclass(slots=True)
class LegalParagraph:
    """Én paragraf med sin plads i loven.

    Dette er systemets primære retrieval-enhed: én paragraf = én
    hovedvektorpost, og et søgeresultat peger på netop den bestemmelse,
    brugeren skal læse.
    """

    number: str
    letter: str | None
    text: str
    char_start: int
    char_end: int
    chapter_no: str | None = None
    chapter_title: str | None = None
    section_no: str | None = None
    section_title: str | None = None
    #: Nærmeste unummererede mellemoverskrift over paragraffen.
    heading: str | None = None
    subsections: list[LegalSubsection] = field(default_factory=list)

    @property
    def paragraph_id(self) -> str:
        return f"§ {self.number}{(' ' + self.letter) if self.letter else ''}"

    @property
    def sort_key(self) -> str:
        return paragraph_sort_key(self.number, self.letter)

    @property
    def chapter_label(self) -> str | None:
        """"Kapitel 3" eller "Kapitel 3 — Skibets drift"."""
        if not self.chapter_no:
            return None
        base = f"Kapitel {self.chapter_no}"
        return f"{base} — {self.chapter_title}" if self.chapter_title else base

    @property
    def legal_path(self) -> str:
        """Paragraffens adresse i loven: "Kapitel 3 · § 12".

        Kapitel OG paragraf, ikke bare den nærmeste af dem: "§ 12" alene
        siger intet om, hvad reglen handler om, og to forskellige loves
        § 12 ser ens ud uden kapitlet.
        """
        parts = [p for p in (self.chapter_label, self.paragraph_id) if p]
        return " · ".join(parts)

    def citation(self, document_title: str | None = None) -> str:
        """Fuld henvisning: "Lov om sikkerhed til søs § 12, kapitel 3"."""
        head = normalize_whitespace(document_title or "")
        tail = self.paragraph_id
        if self.chapter_no:
            tail = f"{tail}, kapitel {self.chapter_no}"
        return f"{head} {tail}".strip()

    def to_metadata(self, *, document_title: str | None = None) -> dict:
        """Feltsættet der følger med paragraffen ind i indeks og API."""
        return {
            "chapter_no": self.chapter_no,
            "chapter_title": self.chapter_title,
            "section_no": self.section_no,
            "section_title": self.section_title or self.heading,
            "paragraph_id": self.paragraph_id,
            "paragraph_sort_key": self.sort_key,
            "full_citation": self.citation(document_title),
        }


@dataclass(slots=True)
class LegalChapter:
    """Et kapitel — sekundær enhed, brugbar til navigation og oversigt."""

    number: str
    title: str | None
    char_start: int
    char_end: int
    section_no: str | None = None
    section_title: str | None = None
    paragraph_ids: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        base = f"Kapitel {self.number}"
        return f"{base} — {self.title}" if self.title else base


@dataclass(slots=True)
class LegalDocumentStructure:
    """Resultatet af at læse et dokuments juridiske form."""

    text: str
    preamble: str = ""
    preamble_end: int = 0
    chapters: list[LegalChapter] = field(default_factory=list)
    paragraphs: list[LegalParagraph] = field(default_factory=list)

    @property
    def has_paragraphs(self) -> bool:
        return bool(self.paragraphs)

    @property
    def body(self) -> str:
        """Teksten uden præambel — det brugeren skal læse først."""
        return self.text[self.preamble_end:].strip()

    def paragraph(self, paragraph_id: str) -> LegalParagraph | None:
        wanted = normalize_whitespace(paragraph_id).lower()
        for item in self.paragraphs:
            if item.paragraph_id.lower() == wanted:
                return item
        return None


# --- Selve parsingen -------------------------------------------------------


def _looks_like_heading(line: str) -> bool:
    """Er linjen en unummereret mellemoverskrift?

    Kriterierne er bevidst konservative: en fejlagtig overskrift er
    kosmetisk, mens en overset paragraf ville koste et søgeresultat.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > _HEADING_MAX_CHARS:
        return False
    if stripped.endswith((".", ":", ";", ",")):
        return False
    if _PARAGRAPH_RE.match(stripped) or _SUBSECTION_RE.match(stripped):
        return False
    if _CHAPTER_RE.match(stripped) or _PART_RE.match(stripped):
        return False
    # En overskrift er ikke en opremsning ("1) ...") og ikke en henvisning.
    if re.match(r"^\d+\)", stripped):
        return False
    return True


def _roman_or_arabic(value: str) -> str:
    return normalize_whitespace(value).replace(" ", "")


def _is_opener_position(before: str) -> bool:
    """Kan en ny bestemmelse begynde her?

    `before` er teksten i segmentet frem til kandidaten.

    Et krav om, at der SKAL stå et punktum lige før, var det første
    forsøg. Det er for stramt: efter en kapiteloverskrift står der intet
    punktum, og "Kapitel 1 Anvendelsesområde § 1. Bekendtgørelsen …" ville
    da miste § 1. Til gengæld er det ikke nødvendigt, fordi tre andre værn
    står tilbage — og en henvisning i dansk lovsprog indledes praktisk
    talt altid af et af de ord, der afvises her:

    1. forkortelse foran ("jf.", "nr.", "stk.") — dette værn,
    2. henvisningsord foran ("i", "efter", "og", "dog") — dette værn,
    3. stort begyndelsesbogstav efter — mønstret selv,
    4. stigende nummerering — :func:`_flat_openers`.
    """
    if not before.strip():
        return True  # segmentets begyndelse
    if _ABBREVIATION_BEFORE_RE.search(before):
        return False
    return not _REFERENCE_CUE_RE.search(before)


def _paragraph_key(number: int, letter: str | None) -> tuple[int, int]:
    """Sorterbar nøgle for en paragraf, så § 12 a følger efter § 12.

    Uden bogstavet ville monotonicitetskravet forkaste "§ 12 a", fordi 12
    ikke er større end 12 — og litra-paragraffer er almindelige i dansk
    lovgivning, netop dér hvor der er indsat noget senere.
    """
    return (number, ord(letter.lower()) - 96 if letter else 0)


def _flat_openers(
    segment: str,
    *,
    last_paragraph: tuple[int, int],
    last_chapter: int,
) -> list[tuple[int, str, tuple[int, int] | int]]:
    """Validerede åbnere midt i et segment.

    Returnerer ``(position, art, nummer)`` sorteret efter position. Kun
    kandidater der både står på en sætningsgrænse OG fortsætter
    nummereringen accepteres.

    Monotonicitetskravet er det andet værn mod krydshenvisninger: står
    "§ 4" inde i § 12, er 4 < 12, og kandidaten forkastes uanset hvad der
    står foran den. De to værn fanger hver sine tilfælde, og begge er
    nødvendige.
    """
    found: list[tuple[int, str, int]] = []

    for match in _FLAT_CHAPTER_RE.finditer(segment):
        if match.start() == 0:
            continue  # allerede segmentets egen indledning
        number = _roman_or_arabic(match.group("number"))
        if not number.isdigit():
            continue  # romertal kan ikke sammenlignes trygt
        if int(number) <= last_chapter:
            continue
        if _is_opener_position(segment[: match.start()]):
            found.append((match.start(), "chapter", int(number)))

    for match in _FLAT_PARAGRAPH_RE.finditer(segment):
        if match.start() == 0:
            continue
        key = _paragraph_key(int(match.group("number")), match.group("letter"))
        if key <= last_paragraph:
            continue
        if _is_opener_position(segment[: match.start()]):
            found.append((match.start(), "paragraph", key))

    for match in _FLAT_SUBSECTION_RE.finditer(segment):
        if match.start() == 0:
            continue
        if _is_opener_position(segment[: match.start()]):
            found.append((match.start(), "subsection", (int(match.group("number")), 0)))

    found.sort(key=lambda item: item[0])

    # Efter et accepteret paragrafskift gælder monotonicitetskravet fra det
    # NYE nummer. Uden det ville "§ 5. ... jf. § 3. ..." acceptere § 3.
    accepted: list[tuple[int, str, tuple[int, int] | int]] = []
    running = last_paragraph
    running_chapter = last_chapter
    for position, kind, value in found:
        if kind == "paragraph":
            if value <= running:
                continue
            running = value
        elif kind == "chapter":
            if value <= running_chapter:
                continue
            running_chapter = value
        accepted.append((position, kind, value))

    return accepted


def _segment_text(text: str) -> list[tuple[int, str]]:
    """Deler teksten i logiske linjer med ABSOLUTTE startpositioner.

    Naturlige linjeskift først; derefter deles et segment yderligere ved
    validerede åbnere midt inde i det. Der indsættes ikke tegn og fjernes
    ikke tegn — positionerne peger uændret ind i `text`, så et stykkes
    ``content`` bliver ved med at være præcis ``text[start:end]``.
    """
    segments: list[tuple[int, str]] = []
    last_paragraph: tuple[int, int] = (0, 0)
    last_chapter = 0

    cursor = 0
    for line in text.split("\n"):
        start = cursor
        cursor += len(line) + 1

        stripped = line.strip()
        if not stripped:
            segments.append((start, line))
            continue

        # Segmentets egen indledning opdaterer tællerne, så en efterfølgende
        # kandidat måles mod den rigtige forgænger.
        opening = _PARAGRAPH_RE.match(line)
        if opening:
            try:
                last_paragraph = _paragraph_key(
                    int(opening.group("number")), opening.group("letter")
                )
            except (TypeError, ValueError, IndexError):
                pass
        opening_chapter = _CHAPTER_RE.match(line)
        if opening_chapter:
            number = _roman_or_arabic(opening_chapter.group("number"))
            if number.isdigit():
                last_chapter = int(number)

        openers = _flat_openers(
            line, last_paragraph=last_paragraph, last_chapter=last_chapter
        )
        if not openers:
            segments.append((start, line))
            continue

        cuts = [0, *[position for position, _, _ in openers], len(line)]
        for piece_start, piece_end in zip(cuts[:-1], cuts[1:], strict=True):
            piece = line[piece_start:piece_end]
            if piece.strip():
                segments.append((start + piece_start, piece))

        for _, kind, value in openers:
            if kind == "paragraph":
                last_paragraph = value
            elif kind == "chapter":
                last_chapter = value

    return segments


def parse_legal_structure(content: str, *, document_title: str | None = None) -> LegalDocumentStructure:
    """Læser en lovtekst og returnerer dens struktur.

    Kaster aldrig. Er teksten tom eller ustruktureret, returneres en
    struktur uden paragraffer, og kalderen må selv vælge en opdeling.
    """
    text = normalize_legal_text(content)
    structure = LegalDocumentStructure(text=text)
    if not text:
        return structure

    # Logiske linjer med absolutte positioner. Er dokumentet leveret på én
    # linje — hvilket kilden gør, når XML'en ikke har markup pr.
    # bestemmelse — deles det her ved validerede åbnere. Se `_segment_text`.
    segments = _segment_text(text)
    lines = [segment for _, segment in segments]
    offsets = [start for start, _ in segments]

    current_part: tuple[str, str | None] | None = None
    current_chapter: LegalChapter | None = None
    current_heading: str | None = None
    current_paragraph: LegalParagraph | None = None
    current_subsection: LegalSubsection | None = None
    first_structural_line: int | None = None

    def close_subsection(end: int) -> None:
        nonlocal current_subsection
        if current_subsection is not None:
            current_subsection.char_end = end
            current_subsection.text = text[current_subsection.char_start:end].strip()
            current_subsection = None

    def close_paragraph(end: int) -> None:
        nonlocal current_paragraph
        close_subsection(end)
        if current_paragraph is not None:
            current_paragraph.char_end = end
            current_paragraph.text = text[current_paragraph.char_start:end].strip()
            current_paragraph = None

    def close_chapter(end: int) -> None:
        nonlocal current_chapter
        if current_chapter is not None:
            current_chapter.char_end = end
            structure.chapters.append(current_chapter)
            current_chapter = None

    for index, line in enumerate(lines):
        start = offsets[index]
        stripped = line.strip()
        if not stripped:
            continue

        part_match = _PART_RE.match(stripped)
        chapter_match = _CHAPTER_RE.match(stripped)
        paragraph_match = _PARAGRAPH_RE.match(stripped)
        subsection_match = _SUBSECTION_RE.match(stripped)

        if part_match:
            close_paragraph(start)
            close_chapter(start)
            title = normalize_whitespace(part_match.group("title")) or None
            if title is None and index + 1 < len(lines) and _looks_like_heading(lines[index + 1]):
                title = normalize_whitespace(lines[index + 1])
            current_part = (_roman_or_arabic(part_match.group("number")), title)
            current_heading = None
            if first_structural_line is None:
                first_structural_line = start
            continue

        if chapter_match:
            close_paragraph(start)
            close_chapter(start)
            title = normalize_whitespace(chapter_match.group("title")) or None
            if title is None and index + 1 < len(lines) and _looks_like_heading(lines[index + 1]):
                title = normalize_whitespace(lines[index + 1])
            current_chapter = LegalChapter(
                number=_roman_or_arabic(chapter_match.group("number")),
                title=title,
                char_start=start,
                char_end=start,
                section_no=current_part[0] if current_part else None,
                section_title=current_part[1] if current_part else None,
            )
            current_heading = None
            if first_structural_line is None:
                first_structural_line = start
            continue

        if paragraph_match:
            close_paragraph(start)
            paragraph = LegalParagraph(
                number=paragraph_match.group("number"),
                letter=(paragraph_match.group("letter") or None),
                text="",
                char_start=start,
                char_end=start,
                chapter_no=current_chapter.number if current_chapter else None,
                chapter_title=current_chapter.title if current_chapter else None,
                section_no=current_part[0] if current_part else None,
                section_title=current_part[1] if current_part else None,
                heading=current_heading,
            )
            structure.paragraphs.append(paragraph)
            if current_chapter is not None:
                current_chapter.paragraph_ids.append(paragraph.paragraph_id)
            current_paragraph = paragraph
            if first_structural_line is None:
                first_structural_line = start
            continue

        if subsection_match and current_paragraph is not None:
            close_subsection(start)
            current_subsection = LegalSubsection(
                number=int(subsection_match.group("number")),
                text="",
                char_start=start,
                char_end=start,
            )
            current_paragraph.subsections.append(current_subsection)
            continue

        # En mellemoverskrift gælder for de paragraffer der følger efter.
        # Kun uden for en igangværende paragraf: en kort linje inde i en
        # paragraf er tekst, ikke en overskrift.
        if current_paragraph is None and _looks_like_heading(stripped):
            # Titlen på et kapitel er allerede opsamlet ovenfor; undgå at
            # den også bliver mellemoverskrift.
            if current_chapter is not None and current_chapter.title == normalize_whitespace(stripped):
                continue
            current_heading = normalize_whitespace(stripped)

    end_of_text = len(text)
    close_paragraph(end_of_text)
    close_chapter(end_of_text)

    # --- Præambel ---------------------------------------------------------
    # Alt før den første strukturelle markør. Findes ingen markør, er der
    # ingen præambel — så er hele teksten brødtekst.
    if first_structural_line:
        candidate = text[:first_structural_line].strip()
        # Kun tekst der faktisk ligner en kundgørelsesformel eller en
        # indledning gemmes som præambel. En enkelt overskriftslinje er
        # ikke en præambel og skal ikke skjules bag et fold-ud.
        if candidate and (_PROMULGATION_RE.search(candidate) or len(candidate) > 120):
            structure.preamble = candidate
            structure.preamble_end = first_structural_line

    structure.paragraphs.sort(key=lambda p: p.char_start)
    return structure

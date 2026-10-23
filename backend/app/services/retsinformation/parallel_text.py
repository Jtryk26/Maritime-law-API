"""To-spaltede traktattekster: engelsk original og dansk oversættelse side om side.

Problemet
=========
Bekendtgørelser af internationale overenskomster (BKI, Lovtidende C) trykkes
med den autentiske engelske tekst i venstre spalte og den danske oversættelse
i højre. I kildens markup er det en tabel med én række pr. trykt linje::

    <tr><td>INTERNATIONAL CONVENTION ON THE CON-</td><td>INTERNATIONAL KONVENTION OM BE-</td></tr>
    <tr><td>TROL OF HARMFUL ANTI-FOULING</td><td>KONTROL MED SKADELIGE BUNDMALINGS-</td></tr>

Læses tabellen vandret, flettes de to sprog linje for linje, og
orddelingerne ved linjeskift bliver stående ("CON-", "BE-").

Løsningen
=========
1. **Find tabellerne.** En tabel regnes for parallelspaltet, når de fleste
   rækker kan deles om en fælles *rendesten* — en kolonne, som ingen
   tekstcelle dækker — og de to halvdele er på hvert sit sprog. Sproget
   afgøres af hyppige småord ("the", "shall" mod "og", "skal") — ikke af
   positionen, så en tabel med spalterne byttet om læses rigtigt, og en
   almindelig dansk tabel med to spalter aldrig forveksles.
2. **Del rækkerne efter kolonneposition**, ikke efter celleantal.
   Retsinformations BKI-tabeller (fx BKI nr. 39 af 2008) bruger ``colspan``:
   engelsk over kolonne 0–9, en tom rendesten over 10–12 og dansk over
   13–20. Listepunkter deler mærke og tekst i hver sin celle
   (``(a)`` | ``ships entitled …``); de samles til ét afsnit. Rækker, hvor
   en tekstcelle dækker rendestenen (bilagstabeller i fuld bredde), tildeles
   et sprog for sig.
3. **Saml hver spalte for sig** til sammenhængende tekst:

   * orddeling ved linjeskift fjernes ("CON-" + "TROL" -> "CONTROL"),
   * men en ægte bindestreg bevares, når ordet forekommer med bindestreg
     andetsteds i spalten ("ANTI-" + "FOULING" -> "ANTI-FOULING"), ved
     forkortelser ("SOLAS-" + "konventionen") og foran "og"/"eller"
     ("skibs- og flysikkerhed"),
   * et nyt afsnit begynder ved overskrifter (versaler), artikelnumre og
     når en linje slutter et punktum og er kortere end spaltens typiske
     linje — den klassiske sætternes markering af afsnitsslut.

   Er rækkerne allerede hele afsnit (som i Retsinformations XML), er hver
   række sit eget afsnit; kun overskrifter over flere rækker sættes sammen.

Modulet er rene funktioner på ``ElementTree``-elementer og tekst; det kender
ikke resten af importen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median
from xml.etree import ElementTree

from app.core.text import normalize_whitespace

__all__ = [
    "ParallelColumns",
    "find_parallel_tables",
    "join_column_lines",
    "language_score",
]

_ROW_NAMES = frozenset({"tr", "row", "raekke", "række"})
_CELL_NAMES = frozenset({"td", "th", "entry", "cell", "celle"})
_TABLE_NAMES = frozenset({"table", "tabel", "tgroup"})

#: Hyppige funktionsord. Ord, der findes i begge sprog ("i", "a"), er udeladt.
_EN_WORDS = frozenset(
    "the of and to shall in any be which this that or by with from such "
    "convention article party parties ship ships regulation annex organization "
    "state states their its into within upon".split()
)
_DA_WORDS = frozenset(
    "og af til skal den det der som eller med fra denne dette disse sådan "
    "konvention artikel part parter skib skibe regel bilag organisation "
    "stat stater deres sin sine inden efter".split()
)

#: Mindst så mange ord i hver spalte, før sproget afgøres.
_MIN_WORDS = 12
#: Andel af tekstrækkerne, der skal kunne deles om rendestenen.
_MIN_SPLIT_SHARE = 0.6
#: En kolonne er rendesten, når højst denne andel af tekstrækkerne dækker den.
_GUTTER_MAX_USE = 0.15

#: Blokelementer i en celle: hver bliver sit eget tekststykke.
_BLOCK_NAMES = frozenset({"exitus", "linea", "p", "para", "div", "br", "li"})
_SUPERSCRIPT = str.maketrans("0123456789()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁽⁾")
_SOFT_HYPHEN = "\u00ad"


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _is_superscript(element: ElementTree.Element) -> bool:
    style = " ".join(str(v) for v in element.attrib.values()).lower()
    return _local(element.tag) == "sup" or "superscript" in style


def _join_pieces(pieces: list[str]) -> str:
    """Sætter en celles linjer sammen; "Antifouling-" + "systemer" bevarer bindestregen."""
    text = ""
    for piece in pieces:
        if not text:
            text = piece
        elif re.search(r"[^\W\d_]-$", text) and piece[:1].islower():
            text += piece
        else:
            text += f" {piece}"
    return text


def _cell_text(cell: ElementTree.Element) -> str:
    """Cellens tekst.

    LexDania deler en sætning i ``<Char>``-elementer efter formatering
    (``<Char formaChar="Bold">DER BEMÆRKER</Char><Char>, at …</Char>``). De
    står adskilt af layout-whitespace, som IKKE er et mellemrum i teksten —
    de egentlige mellemrum ligger inde i ``Char``. Whitespace-only tekst
    springes derfor over, og kun blokelementer giver ordskel. Bløde
    bindestreger fjernes, og hævet skrift (fodnotemærker) bliver til
    hævede tegn.
    """
    pieces: list[list[str]] = [[]]

    def add(value: str | None, superscript: bool = False) -> None:
        if value and value.strip():
            pieces[-1].append(value.translate(_SUPERSCRIPT) if superscript else value)

    def walk(node: ElementTree.Element) -> None:
        block = _local(node.tag) in _BLOCK_NAMES
        if block:
            pieces.append([])
        add(node.text, _is_superscript(node))
        for child in node:
            walk(child)
            add(child.tail)
        if block:
            pieces.append([])

    walk(cell)
    lines = [normalize_whitespace("".join(p).replace(_SOFT_HYPHEN, "")) for p in pieces]
    return _join_pieces([line for line in lines if line])


def language_score(text: str, *, min_words: int = _MIN_WORDS) -> float:
    """-1 (dansk) … +1 (engelsk). 0 ved for lidt tekst eller lige mange."""
    words = re.findall(r"[a-zæøå]+", text.lower())
    if len(words) < min_words:
        return 0.0
    en = sum(word in _EN_WORDS for word in words)
    da = sum(word in _DA_WORDS for word in words)
    if en + da == 0:
        return 0.0
    return (en - da) / (en + da)


@dataclass(slots=True)
class ParallelColumns:
    """Én parallelspaltet tabel, delt i sine to sprog."""

    english: list[str] = field(default_factory=list)
    danish: list[str] = field(default_factory=list)
    #: "left" eller "right": hvor den engelske tekst stod.
    english_side: str = "left"


@dataclass(slots=True)
class _Cell:
    start: int
    end: int  # eksklusiv
    text: str


def _rows(table: ElementTree.Element) -> list[list[_Cell]]:
    """Tabellens rækker med hver celles kolonneposition (``colspan`` medregnet)."""
    rows: list[list[_Cell]] = []
    for element in table.iter():
        if _local(element.tag) not in _ROW_NAMES:
            continue
        cells: list[_Cell] = []
        column = 0
        for child in element:
            if _local(child.tag) not in _CELL_NAMES:
                continue
            try:
                span = max(1, int(child.attrib.get("colspan", 1)))
            except ValueError:
                span = 1
            cells.append(_Cell(column, column + span, _cell_text(child)))
            column += span
        rows.append(cells)
    return rows


def _gutter(rows: list[list[_Cell]]) -> tuple[int, int]:
    """Rendestenen som halvåbent kolonneinterval ``[lo, hi)``.

    Den længste indre række af kolonner, som (næsten) ingen tekstcelle
    dækker. Findes ingen — en tabel med to celler pr. række og ingen
    ``colspan`` — er rendestenen den tomme grænse midt i tabellen.
    """
    width = max((cells[-1].end for cells in rows if cells), default=0)
    text_rows = [cells for cells in rows if any(c.text for c in cells)]
    use = [0] * width
    for cells in text_rows:
        for cell in cells:
            if cell.text:
                for column in range(cell.start, cell.end):
                    use[column] += 1
    limit = _GUTTER_MAX_USE * len(text_rows)
    best: tuple[int, int] = (width // 2, width // 2)
    column = 1
    while column < width - 1:
        if use[column] <= limit:
            run_end = column
            while run_end < width - 1 and use[run_end] <= limit:
                run_end += 1
            if run_end - column > best[1] - best[0]:
                best = (column, run_end)
            column = run_end
        column += 1
    return best


def _crosses(cell: _Cell, gutter: tuple[int, int]) -> bool:
    lo, hi = gutter
    if lo == hi:
        return cell.start < lo < cell.end
    return cell.start < hi and cell.end > lo


def _side_text(cells: list[_Cell]) -> str:
    return normalize_whitespace(" ".join(c.text for c in cells if c.text))


def _span_language(text: str, previous: str | None) -> str | None:
    """Sproget for en række i fuld bredde: småord, ellers overskriftsord, ellers forrige."""
    score = language_score(text, min_words=6)
    if score > 0.2:
        return "en"
    if score < -0.2:
        return "da"
    first = text.split(" ", 1)[0].lower()
    if first in {"annex", "article", "regulation", "appendix", "chapter"}:
        return "en"
    if first in {"bilag", "artikel", "regel", "tillæg", "kapitel"}:
        return "da"
    return previous


def _as_parallel(table: ElementTree.Element) -> ParallelColumns | None:
    rows = _rows(table)
    text_rows = [cells for cells in rows if any(c.text for c in cells)]
    if len(text_rows) < 3:
        return None
    gutter = _gutter(rows)

    def is_split(cells: list[_Cell]) -> bool:
        return not any(c.text and _crosses(c, gutter) for c in cells)

    split_rows = [cells for cells in text_rows if is_split(cells)]
    if len(split_rows) / len(text_rows) < _MIN_SPLIT_SHARE:
        return None

    lo, hi = gutter
    # Tomme rækker beholdes i spalterne: de markerer afsnitsskift.
    left: list[str] = []
    right: list[str] = []
    spanning: list[tuple[int, int, str]] = []  # (række, plads i venstre/højre, tekst)
    for cells in rows:
        if not is_split(cells):
            text = " | ".join(c.text for c in cells if c.text)
            spanning.append((len(left), len(right), text))
            continue
        left.append(_side_text([c for c in cells if c.end <= lo]))
        right.append(_side_text([c for c in cells if c.start >= max(hi, lo)]))

    left_score = language_score(" ".join(left))
    right_score = language_score(" ".join(right))
    # Spalterne skal være på hvert sit sprog — tydeligt.
    if left_score > 0.2 and right_score < -0.2:
        english_side = "left"
    elif right_score > 0.2 and left_score < -0.2:
        english_side = "right"
    else:
        return None

    english, danish = (left, right) if english_side == "left" else (right, left)
    # Rækker i fuld bredde sættes ind bagfra, så de tidligere pladser holder
    # — og rækker med samme plads beholder deres indbyrdes rækkefølge.
    language: str | None = None
    placed: list[tuple[int, list[str], str]] = []
    for left_at, right_at, text in spanning:
        language = _span_language(text, language)
        if language is None:
            continue
        target = english if language == "en" else danish
        at = left_at if (target is left) else right_at
        placed.append((at, target, text))
    for at, target, text in reversed(placed):
        target[at:at] = ["", text, ""]
    return ParallelColumns(english=english, danish=danish, english_side=english_side)


def find_parallel_tables(root: ElementTree.Element) -> dict[int, ParallelColumns]:
    """Parallelspaltede tabeller i dokumentet, nøglet på ``id(element)``.

    Kun yderste tabeller: en tabel inde i en parallelspaltet tabel hører
    til dens tekst.
    """
    found: dict[int, ParallelColumns] = {}
    claimed: set[int] = set()
    for element in root.iter():
        if id(element) in claimed or _local(element.tag) not in _TABLE_NAMES:
            continue
        columns = _as_parallel(element)
        if columns is None:
            continue
        found[id(element)] = columns
        claimed.update(id(child) for child in element.iter())
    return found


# -- Samling af en spalte ------------------------------------------------------

_BLOCK_START_RE = re.compile(
    r"^(?:article|artikel|regulation|regel|annex|bilag|chapter|kapitel|appendix|tillæg|"
    r"part|del|section|afsnit)\b"
    r"|^\(?[0-9]+[a-z]?[.)]\s"
    r"|^\([a-z]{1,3}\)\s"
    r"|^§\s*\d",
    re.IGNORECASE,
)
#: Præamblens betragtninger begynder hver sit afsnit: "NOTING that …",
#: "HAVE AGREED as follows:", "SOM BEMÆRKER, at …". En fast liste frem for
#: en regel om versaler, som også ville splitte ved "SOLAS applies".
_RECITAL_RE = re.compile(
    r"^(?:NOTING|NOTING ALSO|RECOGNIZING|RECOGNISING|CONSIDERING|DESIRING|RECALLING|"
    r"BEARING IN MIND|CONVINCED|CONCERNED|MINDFUL|AWARE|EMPHASIZING|EMPHASISING|"
    r"ACKNOWLEDGING|DETERMINED|BELIEVING|HAVING|HAVE AGREED|HAS AGREED|"
    r"SOM|DER|IDET|ER BLEVET ENIGE|ER BLEVET ENIGE OM|I ERKENDELSE|I BETRAGTNING|"
    r"UNDER HENVISNING|UNDER HENSYNTAGEN)\b(?=[ ,])"
)
_SENTENCE_END_RE = re.compile(r"[.:;!?]\W*$")
_CONJUNCTIONS = frozenset({"og", "eller", "samt", "and", "or", "nor"})
#: Er spaltens typiske række mindst så lang, er rækkerne hele afsnit, ikke
#: trykte linjer.
_PARAGRAPH_ROW_LENGTH = 90


def _is_heading(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 3 and sum(c.isupper() for c in letters) / len(letters) > 0.85


def _hyphenated_words(lines: list[str]) -> set[str]:
    """Ord med bindestreg INDE i en linje — dem må en linjeende ikke ødelægge."""
    words: set[str] = set()
    for line in lines:
        for match in re.finditer(r"\b([^\W\d_]+-[^\W\d_]+)\b", line):
            words.add(match.group(1).lower())
    return words


def _join_hyphen(previous: str, line: str, keep: set[str]) -> str:
    """Sætter en linje, der fortsætter et ord delt med bindestreg, på."""
    fragment = re.search(r"([^\W\d_]+)-$", previous)
    next_word = re.match(r"([^\W\d_]+)", line)
    if not fragment or not next_word:
        return f"{previous} {line}"
    head, tail = fragment.group(1), next_word.group(1)
    if tail.lower() in _CONJUNCTIONS:  # "skibs- og flysikkerhed"
        return f"{previous} {line}"
    if f"{head}-{tail}".lower() in keep:  # "ANTI-" + "FOULING"
        return f"{previous}{line}"
    if head.isupper() and len(head) >= 2 and tail[0].islower():  # "SOLAS-" + "konventionen"
        return f"{previous}{line}"
    return f"{previous[:-1]}{line}"  # orddeling: "CON-" + "TROL"


def join_column_lines(lines: list[str]) -> str:
    """Samler en spaltes trykte linjer til afsnit, ét afsnit pr. linje i resultatet.

    Er rækkerne allerede afsnit (lang typisk række), er hver række sit eget
    afsnit; kun overskrifter over flere rækker sættes sammen.
    """
    cleaned = [normalize_whitespace(line) for line in lines]
    nonempty = [line for line in cleaned if line]
    if not nonempty:
        return ""
    typical = median(len(line) for line in nonempty)
    keep = _hyphenated_words(nonempty)
    rows_are_paragraphs = typical >= _PARAGRAPH_ROW_LENGTH

    paragraphs: list[str] = []
    previous_raw = ""  # forrige linje, som den stod — dens længde afslører afsnitsslut
    for line in cleaned:
        if not line:  # tom række = afsnitsskift
            if paragraphs and paragraphs[-1]:
                paragraphs.append("")
            previous_raw = ""
            continue
        if not paragraphs or not paragraphs[-1]:
            if paragraphs and not paragraphs[-1]:
                paragraphs[-1] = line
            else:
                paragraphs.append(line)
            previous_raw = line
            continue

        current = paragraphs[-1]
        heading_now, heading_before = _is_heading(line), _is_heading(previous_raw)
        if heading_before and heading_now:
            new_paragraph = False  # en overskrift over flere linjer
        elif rows_are_paragraphs:
            new_paragraph = True
        elif heading_before != heading_now:
            new_paragraph = True
        elif _BLOCK_START_RE.match(line) or _RECITAL_RE.match(line):
            new_paragraph = True
        elif _BLOCK_START_RE.match(previous_raw) and len(previous_raw) < 0.5 * typical:
            new_paragraph = True  # "Article 1" på egen linje, overskriften under
        else:
            short = len(previous_raw) < 0.75 * typical
            new_paragraph = bool(_SENTENCE_END_RE.search(previous_raw)) and short

        if new_paragraph:
            paragraphs.append(line)
        elif re.search(r"[^\W\d_]-$", current):
            paragraphs[-1] = _join_hyphen(current, line, keep)
        else:
            paragraphs[-1] = f"{current} {line}"
        previous_raw = line

    return "\n".join(p for p in paragraphs if p)

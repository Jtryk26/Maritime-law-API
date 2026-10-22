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
   rækker har præcis to celler, og de to spalter er på hvert sit sprog.
   Sproget afgøres af hyppige småord ("the", "shall" mod "og", "skal") —
   ikke af positionen, så en tabel med spalterne byttet om læses rigtigt,
   og en almindelig dansk tabel med to spalter aldrig forveksles.
2. **Saml hver spalte for sig** til sammenhængende tekst:

   * orddeling ved linjeskift fjernes ("CON-" + "TROL" -> "CONTROL"),
   * men en ægte bindestreg bevares, når ordet forekommer med bindestreg
     andetsteds i spalten ("ANTI-" + "FOULING" -> "ANTI-FOULING"), ved
     forkortelser ("SOLAS-" + "konventionen") og foran "og"/"eller"
     ("skibs- og flysikkerhed"),
   * et nyt afsnit begynder ved overskrifter (versaler), artikelnumre og
     når en linje slutter et punktum og er kortere end spaltens typiske
     linje — den klassiske sætternes markering af afsnitsslut.

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
#: Andel af tekstrækkerne, der skal have præcis to celler.
_MIN_TWO_CELL_SHARE = 0.6


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _cell_text(cell: ElementTree.Element) -> str:
    return normalize_whitespace(" ".join(cell.itertext()))


def language_score(text: str) -> float:
    """-1 (dansk) … +1 (engelsk). 0 ved for lidt tekst eller lige mange."""
    words = re.findall(r"[a-zæøå]+", text.lower())
    if len(words) < _MIN_WORDS:
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


def _rows(table: ElementTree.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for element in table.iter():
        if _local(element.tag) not in _ROW_NAMES:
            continue
        cells = [_cell_text(c) for c in element if _local(c.tag) in _CELL_NAMES]
        rows.append(cells)
    return rows


def _as_parallel(table: ElementTree.Element) -> ParallelColumns | None:
    rows = _rows(table)
    text_rows = [r for r in rows if any(r)]
    if len(text_rows) < 3:
        return None
    two = [r for r in text_rows if len(r) == 2]
    if len(two) / len(text_rows) < _MIN_TWO_CELL_SHARE:
        return None

    # Tomme rækker beholdes i spalterne: de markerer afsnitsskift.
    left = [r[0] for r in rows if len(r) == 2]
    right = [r[1] for r in rows if len(r) == 2]
    left_score = language_score(" ".join(left))
    right_score = language_score(" ".join(right))
    # Spalterne skal være på hvert sit sprog — tydeligt.
    if left_score > 0.2 and right_score < -0.2:
        return ParallelColumns(english=left, danish=right, english_side="left")
    if right_score > 0.2 and left_score < -0.2:
        return ParallelColumns(english=right, danish=left, english_side="right")
    return None


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
    """Samler en spaltes trykte linjer til afsnit, ét afsnit pr. linje i resultatet."""
    cleaned = [normalize_whitespace(line) for line in lines]
    nonempty = [line for line in cleaned if line]
    if not nonempty:
        return ""
    typical = median(len(line) for line in nonempty)
    keep = _hyphenated_words(nonempty)

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

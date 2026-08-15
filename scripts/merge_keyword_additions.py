#!/usr/bin/env python3
"""Flet ``maritime_keywords.additions.yaml`` ind i en ny nøgleordskonfiguration.

Produktionsfilen læses, men skrives ALDRIG. Resultatet lægges i en ny fil
(standard: ``config/maritime_keywords.after.yaml``), så den nye konfiguration
kan måles med ``scripts/rescore_manifest.py``, før nogen beslutter at gøre den
til produktionsfilen.

Hvorfor tekstuel fletning og ikke ``yaml.safe_load`` + ``yaml.dump``
===================================================================
En rundtur gennem PyYAML ville kaste samtlige kommentarer, afsnitsoverskrifter
og feltrækkefølge væk. ``maritime_keywords.yaml`` er en fil, mennesker
redigerer i hånden — kommentarerne dér forklarer, hvorfor "lods" er et regex
og hvorfor "havn" kun vejer 5.0. De er en del af filens værdi.

Scriptet arbejder derfor på linjer: det finder ``terms:``- og
``negative_terms:``-sektionerne, erstatter de poster der skal ændres, og
tilføjer nye poster i bunden af den rette sektion. Alt andet i filen står
tegn for tegn som før.

YAML-parseren bruges kun til KONTROL bagefter — se ``validate()``.

Brug
====
    python3 scripts/merge_keyword_additions.py \
        --base config/maritime_keywords.yaml \
        --additions config/maritime_keywords.additions.yaml \
        --out config/maritime_keywords.after.yaml

Derefter::

    PYTHONPATH=backend python3 scripts/rescore_manifest.py \
        manifests/discovery-global.csv \
        --config config/maritime_keywords.after.yaml \
        --changed-only --out /tmp/rescore-efter.csv
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

# ---------------------------------------------------------------------------
# Nøgler i additions-filen -> sektion i basisfilen
# ---------------------------------------------------------------------------

ADDITION_KEYS: dict[str, str] = {
    "terms_additions": "terms",
    "terms_additions_fuel": "terms",
    "negative_terms_additions": "negative_terms",
}
CHANGE_KEY = "terms_changed"
CHANGE_SECTION = "terms"

FIELD_ORDER = ("term", "weight", "match", "pattern", "concept")

PROVENANCE = "config/maritime_keywords.additions.yaml"

_FOLD = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "Æ": "ae", "Ø": "oe", "Å": "aa"})


def fold(text: str) -> str:
    """Samme foldning som app/core/text.py — bruges kun til identitetstjek."""
    return str(text).translate(_FOLD).lower().strip()


# ---------------------------------------------------------------------------
# Gengivelse af en post i basisfilens stil
# ---------------------------------------------------------------------------


def format_weight(value: Any) -> str:
    number = float(value)
    return f"{number:.1f}" if number == int(number) else str(number)


def render_scalar(value: Any) -> str:
    """Gengiver en værdi som YAML-skalar, med citationstegn kun hvor det kræves.

    PyYAML sætter et dokumentafslutnings-mærke (``...``) på egen linje efter
    almindelige skalarer. Det fjernes her. Mønstre som ``lods(en|er)?\\b`` og
    termer som ``by & havn`` slipper dermed igennem uden citationstegn, mens
    en værdi som ``a: b`` bliver citeret korrekt.
    """
    dumped = yaml.safe_dump(value, allow_unicode=True, default_flow_style=True)
    lines = [line for line in dumped.splitlines() if line.strip() != "..."]
    return "\n".join(lines).strip()


def render_entry(entry: dict[str, Any], *, indent: str = "  ") -> list[str]:
    """Gengiver én listepost i præcis den blokstil, basisfilen bruger."""
    known = [key for key in FIELD_ORDER if key in entry]
    extra = [key for key in entry if key not in FIELD_ORDER]
    lines: list[str] = []
    for position, key in enumerate([*known, *extra]):
        value = entry[key]
        rendered = format_weight(value) if key == "weight" else render_scalar(value)
        prefix = f"{indent}- " if position == 0 else f"{indent}  "
        lines.append(f"{prefix}{key}: {rendered}")
    return lines


# ---------------------------------------------------------------------------
# Sektions- og postgrænser i basisfilen
# ---------------------------------------------------------------------------

TOP_LEVEL = re.compile(r"^[A-Za-zÆØÅæøå_][\w-]*:\s*$")
ENTRY_START = re.compile(r"^(\s*)-\s")


@dataclass
class Section:
    name: str
    header_index: int      # linjen med "terms:"
    start: int             # første linje efter overskriften
    end: int               # eksklusiv slutgrænse (næste topniveaunøgle)
    indent: str            # indrykning for "- term:" i denne sektion


def find_section(lines: list[str], name: str) -> Section:
    header_index = None
    for index, line in enumerate(lines):
        if TOP_LEVEL.match(line) and line.split(":", 1)[0].strip() == name:
            header_index = index
            break
    if header_index is None:
        raise SystemExit(f"AFBRUDT: sektionen '{name}:' blev ikke fundet i basisfilen.")

    end = len(lines)
    for index in range(header_index + 1, len(lines)):
        if TOP_LEVEL.match(lines[index]):
            end = index
            break

    indent = "  "
    for index in range(header_index + 1, end):
        match = ENTRY_START.match(lines[index])
        if match:
            indent = match.group(1)
            break

    return Section(name=name, header_index=header_index, start=header_index + 1, end=end, indent=indent)


def entry_blocks(lines: list[str], section: Section) -> list[tuple[int, int, str]]:
    """Returnerer (start, slut_eksklusiv, term) for hver post i sektionen."""
    blocks: list[tuple[int, int, str]] = []
    index = section.start
    while index < section.end:
        match = ENTRY_START.match(lines[index])
        if not match:
            index += 1
            continue
        start = index
        index += 1
        # Fortsættelseslinjer: indrykket mere end bindestregen, ikke kommentar/blank.
        while index < section.end:
            line = lines[index]
            if not line.strip() or line.lstrip().startswith("#") or ENTRY_START.match(line):
                break
            index += 1
        block = "\n".join(lines[start:index])
        parsed = yaml.safe_load(block)
        term = ""
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            term = str(parsed[0].get("term", ""))
        blocks.append((start, index, term))
    return blocks


# ---------------------------------------------------------------------------
# Fletningen
# ---------------------------------------------------------------------------


@dataclass
class MergeReport:
    replaced: list[str]
    added: dict[str, list[str]]
    skipped_duplicates: list[str]
    promoted_to_addition: list[str]


def load_additions(path: Path) -> dict[str, list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"AFBRUDT: {path} skal indeholde et YAML-objekt.")

    result: dict[str, list[dict[str, Any]]] = {}
    for key in (*ADDITION_KEYS, CHANGE_KEY):
        entries = data.get(key) or []
        if not isinstance(entries, list):
            raise SystemExit(f"AFBRUDT: '{key}' i {path} skal være en liste.")
        for entry in entries:
            if not isinstance(entry, dict) or "term" not in entry:
                raise SystemExit(f"AFBRUDT: post uden 'term' under '{key}' i {path}.")
        result[key] = entries

    unknown = set(data) - set(result)
    if unknown:
        print(f"  bemærk: ignorerer ukendte sektioner i additions-filen: {sorted(unknown)}")
    return result


def merge(base_lines: list[str], additions: dict[str, list[dict[str, Any]]]) -> tuple[list[str], MergeReport]:
    lines = list(base_lines)
    report = MergeReport(replaced=[], added={}, skipped_duplicates=[], promoted_to_addition=[])

    # -- 1. Erstatninger ----------------------------------------------------
    # Bagfra, så linjenumrene i de øvrige blokke ikke forskydes undervejs.
    section = find_section(lines, CHANGE_SECTION)
    existing = {fold(term): (start, stop) for start, stop, term in entry_blocks(lines, section) if term}

    pending_as_additions: list[dict[str, Any]] = []
    replacements: list[tuple[int, int, dict[str, Any]]] = []
    for entry in additions.get(CHANGE_KEY, []):
        key = fold(entry["term"])
        if key not in existing:
            report.promoted_to_addition.append(str(entry["term"]))
            pending_as_additions.append(entry)
            continue
        start, stop = existing[key]
        replacements.append((start, stop, entry))
        report.replaced.append(str(entry["term"]))

    for start, stop, entry in sorted(replacements, key=lambda item: item[0], reverse=True):
        block = render_entry(entry, indent=section.indent)
        block.insert(0, f"{section.indent}# rettet — jf. {PROVENANCE}")
        lines[start:stop] = block

    # -- 2. Tilføjelser -----------------------------------------------------
    grouped: dict[str, list[dict[str, Any]]] = {}
    for key, target in ADDITION_KEYS.items():
        grouped.setdefault(target, []).extend(additions.get(key, []))
    if pending_as_additions:
        grouped.setdefault(CHANGE_SECTION, []).extend(pending_as_additions)

    # Bagfra efter sektionens placering i filen, af samme grund som ovenfor.
    ordered = sorted(
        grouped.items(),
        key=lambda item: find_section(lines, item[0]).header_index,
        reverse=True,
    )

    for target, entries in ordered:
        section = find_section(lines, target)
        present = {fold(term) for _, _, term in entry_blocks(lines, section) if term}

        fresh: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in entries:
            key = fold(entry["term"])
            if key in present or key in seen:
                report.skipped_duplicates.append(f"{target}: {entry['term']}")
                continue
            seen.add(key)
            fresh.append(entry)

        report.added[target] = [str(entry["term"]) for entry in fresh]
        if not fresh:
            continue

        block: list[str] = [
            "",
            f"{section.indent}# --- Tilføjet fra {PROVENANCE} ---",
        ]
        for entry in fresh:
            block.extend(render_entry(entry, indent=section.indent))

        insert_at = section.end
        while insert_at > section.start and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines[insert_at:insert_at] = block

    return lines, report


# ---------------------------------------------------------------------------
# Kontrol
# ---------------------------------------------------------------------------


def term_key(entry: dict[str, Any]) -> tuple[str, str]:
    return fold(entry.get("term", "")), str(entry.get("match", "word"))


def validate(
    base: dict[str, Any],
    result: dict[str, Any],
    additions: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Returnerer en liste af problemer. Tom liste = i orden."""
    problems: list[str] = []

    # Struktur og uændrede afsnit.
    for key in ("version", "scoring", "thresholds"):
        if base.get(key) != result.get(key):
            problems.append(f"'{key}' blev ændret — det skulle den ikke.")
    if not result.get("terms"):
        problems.append("'terms' er tom eller mangler i resultatet.")

    for section in ("terms", "negative_terms"):
        entries = result.get(section) or []
        # Dubletter.
        seen: dict[tuple[str, str], int] = {}
        for entry in entries:
            key = term_key(entry)
            seen[key] = seen.get(key, 0) + 1
        for key, count in seen.items():
            if count > 1:
                problems.append(f"dublet i '{section}': {key[0]} ({key[1]}) optræder {count} gange")
        # Obligatoriske felter.
        for entry in entries:
            if "term" not in entry or "weight" not in entry:
                problems.append(f"post uden term/weight i '{section}': {entry}")
            if entry.get("match") == "regex" and not entry.get("pattern"):
                problems.append(f"regex-post uden pattern i '{section}': {entry.get('term')}")

    changed = {fold(e["term"]): e for e in additions.get(CHANGE_KEY, [])}
    result_terms = {fold(e.get("term", "")): e for e in result.get("terms") or []}

    # Ingen oprindelige termer må være forsvundet.
    for entry in base.get("terms") or []:
        key = fold(entry.get("term", ""))
        if key not in result_terms:
            problems.append(f"oprindelig term forsvandt fra 'terms': {entry.get('term')}")

    # Ændringer skal være slået igennem.
    for key, entry in changed.items():
        actual = result_terms.get(key)
        if actual is None:
            problems.append(f"ændret term mangler i resultatet: {entry['term']}")
        elif float(actual.get("weight", -1)) != float(entry["weight"]):
            problems.append(
                f"vægt for '{entry['term']}' er {actual.get('weight')}, forventet {entry['weight']}"
            )

    # Tilføjelser skal være til stede.
    for key, target in ADDITION_KEYS.items():
        pool = {fold(e.get("term", "")) for e in result.get(target) or []}
        for entry in additions.get(key, []):
            if fold(entry["term"]) not in pool:
                problems.append(f"tilføjet term mangler i '{target}': {entry['term']}")

    return problems


# ---------------------------------------------------------------------------


def summarise(base: dict[str, Any], result: dict[str, Any]) -> None:
    for section in ("terms", "negative_terms"):
        before = len(base.get(section) or [])
        after = len(result.get(section) or [])
        print(f"  {section:<16}{before:>4} -> {after:>4}   ({after - before:+d})")


def print_list(label: str, values: Iterable[str]) -> None:
    values = list(values)
    if not values:
        return
    print(f"\n{label} ({len(values)}):")
    for value in values:
        print(f"  - {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, default=Path("config/maritime_keywords.yaml"))
    parser.add_argument(
        "--additions", type=Path, default=Path("config/maritime_keywords.additions.yaml")
    )
    parser.add_argument("--out", type=Path, default=Path("config/maritime_keywords.after.yaml"))
    parser.add_argument("--force", action="store_true", help="Overskriv en eksisterende --out.")
    args = parser.parse_args(argv)

    for path in (args.base, args.additions):
        if not path.exists():
            parser.error(f"Filen findes ikke: {path}")

    # Sikring mod at skrive i produktionsfilen.
    if args.out.resolve() == args.base.resolve():
        parser.error("--out må ikke pege på basisfilen. Produktionsfilen skal stå urørt.")
    if args.out.name == "maritime_keywords.yaml":
        parser.error("--out må ikke hedde maritime_keywords.yaml.")
    if args.out.exists() and not args.force:
        parser.error(f"{args.out} findes allerede. Brug --force for at overskrive.")

    base_text = args.base.read_text(encoding="utf-8")
    base_lines = base_text.splitlines()
    base_data = yaml.safe_load(base_text) or {}
    additions = load_additions(args.additions)

    merged_lines, report = merge(base_lines, additions)
    merged_text = "\n".join(merged_lines)
    if base_text.endswith("\n"):
        merged_text += "\n"

    try:
        result_data = yaml.safe_load(merged_text) or {}
    except yaml.YAMLError as exc:
        print("AFBRUDT: resultatet er ikke gyldig YAML. Intet er skrevet.", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 2

    problems = validate(base_data, result_data, additions)

    print(f"Basis     : {args.base}  (urørt)")
    print(f"Tilføjelser: {args.additions}")
    print(f"Resultat  : {args.out}")
    print()
    summarise(base_data, result_data)

    print_list("Erstattede poster", report.replaced)
    for target, terms in report.added.items():
        print_list(f"Tilføjet under '{target}'", terms)
    print_list("Sprunget over (fandtes allerede)", report.skipped_duplicates)
    print_list("Stod som 'ændring', men fandtes ikke — tilføjet i stedet", report.promoted_to_addition)

    if problems:
        print(f"\nKONTROL FEJLEDE ({len(problems)}). Intet er skrevet:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    args.out.write_text(merged_text, encoding="utf-8")
    print("\nKontrol: gyldig YAML, ingen dubletter, ingen tabte termer, alle ændringer slået igennem.")
    print(f"Skrevet: {args.out}")

    # Sidste tjek: kan den rigtige motor indlæse filen?
    try:
        from app.services.relevance.keyword_engine import KeywordRelevanceEngine
    except ImportError:
        print("Bemærk: backend-koden kunne ikke importeres her; motorindlæsning ikke afprøvet.")
        print("        Kør evt.: PYTHONPATH=backend python3 -c \"from app.services.relevance."
              "keyword_engine import KeywordRelevanceEngine as E; "
              f"E(config_path=__import__('pathlib').Path('{args.out}'))\"")
    else:
        engine = KeywordRelevanceEngine(config_path=args.out)
        print(f"Motoren indlæste filen: {len(engine.terms)} termer, "
              f"{len(engine.negative_terms)} negative termer.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

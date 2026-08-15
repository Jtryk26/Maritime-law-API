#!/usr/bin/env python3
"""Regelbaseret oprydning i ``decision``-kolonnen i et discovery-manifest.

Baggrund
========
``discover-global`` forhåndsvurderer hver kandidat med
``KeywordRelevanceEngine`` på titel + myndighed alene (ingen brødtekst) og
sætter ``decision`` ud fra prescoren. Dokumenter i mellemfeltet lander på
``review``. En gennemgang af de 88 review-poster i
``manifests/discovery-global.csv`` viste, at de ikke er støj: hovedparten er
reelt maritime, men faldt under tærsklen fordi termvægtene er for forsigtige
omkring fiskeri, havnesikring og udtrykket "til søs".

Dette script omsætter den gennemgang til en regeltabel, så beslutningen kan
efterprøves, gentages og diskuteres linje for linje — i stedet for at 88
skøn ligger i hovedet på den, der redigerede filen.

Princip: **scriptet rører kun poster med ``decision == review``.** De 372
godkendte ``include``, de 12.359 ``exclude`` og de 2.887 ``skip`` bliver
liggende uændret, og det kontrolleres eksplicit efter skrivning.

Scriptet er bevidst uafhængigt af backend-pakken (kun standardbibliotek), så
det kan køres direkte på VM'en uden at aktivere et virtuelt miljø.

Brug
====
Tørløb (standard — skriver ikke i manifestet)::

    python3 scripts/triage_review.py manifests/discovery-global.csv

Skriv ændringerne::

    python3 scripts/triage_review.py manifests/discovery-global.csv --apply

Behandl støtteordninger som en selvstændig kategori i stedet for include::

    python3 scripts/triage_review.py manifests/discovery-global.csv \
        --support-scheme-decision review --apply

Scriptet skriver altid en rapport ved siden af manifestet
(``discovery-global-triage-report.csv``) med tier, regel og begrundelse pr.
post, så beslutningerne kan gennemgås uden at læse regeltabellen.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Tekstfoldning — samme konvention som app/core/text.py
# ---------------------------------------------------------------------------

_FOLD = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "Æ": "ae", "Ø": "oe", "Å": "aa"})


def fold(text: str) -> str:
    """Folder dansk tekst til ASCII-agtig småbogstavsform.

    ``Søfartsstyrelsen`` -> ``soefartsstyrelsen``. Samme regel anvendes på
    regeludtrykkene, så tabellen nedenfor kan skrives på almindeligt dansk.
    """
    return text.translate(_FOLD).lower()


# ---------------------------------------------------------------------------
# Regeltabellen
# ---------------------------------------------------------------------------
#
# Rækkefølgen er betydningsbærende: første regel der matcher, vinder.
# Grupperne evalueres i denne orden:
#
#   1. exclude       — rammer vores søgeord uden at være maritim regulering
#   2. housekeeping  — administrativ oprydning, kræver menneskeligt skøn
#   3. core          — klassisk søfart, skibsdrift, søret, havnesikring
#   4. support       — tilskuds- og støtteordninger i fiskeri/akvakultur
#   5. fishery       — konkret fiskeriregulering (kvoter, rationer, fartøjer)
#
# ``support`` skal stå FØR ``fishery``, fordi støtteordningerne også nævner
# fiskeri; ``core`` skal stå før begge, fordi et par fiskeridokumenter
# handler om selve fartøjet og ikke om kvoten.


@dataclass(frozen=True)
class Rule:
    tier: str
    name: str
    pattern: str
    reason: str
    unless: str | None = None

    def compiled(self) -> re.Pattern[str]:
        return re.compile(fold(self.pattern))

    def compiled_unless(self) -> re.Pattern[str] | None:
        return re.compile(fold(self.unless)) if self.unless else None


RULES: tuple[Rule, ...] = (
    # -- 1. Udelukkelser ----------------------------------------------------
    Rule(
        tier="exclude",
        name="metro-og-byudvikling",
        pattern=r"cityring|metroselskab|udviklingsselskabet by & havn",
        reason=(
            "Byudvikling og metrobyggeri. 'Nordhavn'/'Sydhavn' er stednavne, "
            "ikke maritim regulering."
        ),
    ),
    Rule(
        tier="exclude",
        name="svovlindhold-generel-braendsel",
        pattern=r"svovlindhold",
        unless=r"marine|skib|bunker|soetransport|skibsbraendstof|havgaaende",
        reason=(
            "Generel regulering af svovl i fyrings- og transportbrændsel. "
            "Rammer termen 'svovlindhold' uden at angå skibsbrændstof."
        ),
    ),
    Rule(
        tier="exclude",
        name="olieraffinaderi",
        pattern=r"olieraffinaderi",
        reason="Landbaseret raffinaderiregulering.",
    ),
    # -- 2. Administrativ oprydning ----------------------------------------
    Rule(
        tier="housekeeping",
        name="ophaevelse-uden-emne",
        pattern=r"ophaevelse af cirkulaere og visse meddelelser|bortfald af visse vejledninger",
        reason=(
            "Ministeriedækkende ophævelse uden konkret emneangivelse. Kan "
            "ramme maritime forskrifter — kræver et opslag i selve teksten."
        ),
    ),
    # -- 3. Maritim kerne ---------------------------------------------------
    Rule(
        tier="core",
        name="til-soes",
        pattern=r"\btil soes\b",
        reason="Udtrykkeligt om forhold til søs.",
    ),
    Rule(
        tier="core",
        name="bjaergning",
        pattern=r"bjaergning",
        reason="Bjærgning og hjælp til søs — klassisk søret.",
    ),
    Rule(
        tier="core",
        name="skib",
        pattern=r"\bskib",
        reason="Handler direkte om skibe eller skibsfart.",
    ),
    Rule(
        tier="core",
        name="soefart-og-soefarende",
        pattern=r"soefart|soefarende|soemand|soefolk|maritim",
        reason="Søfart, søfarende eller maritime forhold i titlen.",
    ),
    Rule(
        tier="core",
        name="redningsraad",
        pattern=r"redningsraad",
        reason="Skibsfartens Redningsråd — eftersøgning og redning til søs.",
    ),
    Rule(
        tier="core",
        name="havnesikring",
        pattern=r"havnefacilitet|sikring af havne|havnesikring",
        reason="Havnesikring (ISPS-området).",
    ),
    Rule(
        tier="core",
        name="modtageordning",
        pattern=r"modtageordning",
        reason="Modtageordninger i havne for olie, kloakspildevand og affald (MARPOL).",
    ),
    Rule(
        tier="core",
        name="udtoemning-bulk",
        pattern=r"udtoemning|transporteres i bulk",
        reason="Udtømning af flydende stoffer transporteret i bulk (MARPOL Annex II).",
    ),
    Rule(
        tier="core",
        name="konvention",
        pattern=r"fiskerikonvention|konvention om fiskeriet|havretskonvention",
        reason="International konvention om havet eller fiskeriet.",
    ),
    Rule(
        tier="core",
        name="fiskerfartoejets-indretning",
        pattern=r"fartoejer, der anvendes til erhvervsmaessigt fiskeri",
        reason="Krav til selve fiskefartøjet — fartøjssikkerhed, ikke kvoteregulering.",
    ),
    Rule(
        tier="core",
        name="om-bord",
        pattern=r"om bord",
        reason="Regulerer forhold om bord på fartøj.",
    ),
    # -- 4. Støtte- og tilskudsordninger ------------------------------------
    Rule(
        tier="support",
        name="tilskud-og-stoette",
        pattern=(
            r"tilskud|de minimis-stoette|stoette til ophugning|ophugning af fiskerfartoejer"
            r"|foerstegangsetablering|producentorganisation|dataindsamling"
            r"|fiskeri- og akvakulturfonden|akvakulturfonden"
        ),
        reason=(
            "Erhvervsstøtte inden for fiskeri/akvakultur. Maritimt domæne, men "
            "tilskudsforvaltning frem for søret eller skibsdrift."
        ),
    ),
    # -- 5. Fiskeriregulering ----------------------------------------------
    Rule(
        tier="fishery",
        name="straksregulering",
        pattern=r"straksregulering",
        reason="Straksregulering af fiskeri — bindende driftsvilkår for fartøjer.",
    ),
    Rule(
        tier="fishery",
        name="fiskerikontrol",
        pattern=r"fiskerikontrol",
        reason="Kontrol med fiskeriet.",
    ),
    Rule(
        tier="fishery",
        name="kvoter-og-rationer",
        pattern=r"kvote|kvotearter|fangstrejseration|kvartalsration|rationsvilkaar|\bration",
        reason="Kvote- og rationsvilkår for fiskefartøjer.",
    ),
    Rule(
        tier="fishery",
        name="fiskeri-generelt",
        pattern=r"\bfiskeri|\bfisker|fartoejsniveau|maf-vilkaar",
        reason="Regulering af fiskeriet som erhverv.",
    ),
)


TIER_LABELS = {
    "core": "maritim kerne",
    "fishery": "fiskeriregulering",
    "support": "støtteordning",
    "housekeeping": "administrativ oprydning",
    "exclude": "ikke maritim",
    "unresolved": "uafklaret",
}


# ---------------------------------------------------------------------------
# Klassifikation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    tier: str
    rule: str
    reason: str


_COMPILED: list[tuple[Rule, re.Pattern[str], re.Pattern[str] | None]] = [
    (r_, r_.compiled(), r_.compiled_unless()) for r_ in RULES
]


def classify(title: str, authority: str = "", document_type: str = "") -> Verdict:
    """Finder første regel der matcher. Uafklaret hvis ingen gør."""
    haystack = fold(" | ".join(part for part in (title, authority, document_type) if part))

    for rule, pattern, unless in _COMPILED:
        if not pattern.search(haystack):
            continue
        if unless is not None and unless.search(haystack):
            continue
        return Verdict(tier=rule.tier, rule=rule.name, reason=rule.reason)

    return Verdict(
        tier="unresolved",
        rule="-",
        reason="Ingen regel matchede. Kræver manuel vurdering.",
    )


# ---------------------------------------------------------------------------
# CSV-håndtering — bevarer BOM, kommentarlinjer, kolonner og rækkefølge
# ---------------------------------------------------------------------------


@dataclass
class Manifest:
    comments: list[str]
    fieldnames: list[str]
    rows: list[dict[str, str]]


def read_manifest(path: Path) -> Manifest:
    raw = path.read_text(encoding="utf-8-sig")
    lines = raw.splitlines(keepends=True)

    comments: list[str] = []
    index = 0
    while index < len(lines) and lines[index].lstrip().startswith("#"):
        comments.append(lines[index])
        index += 1

    reader = csv.DictReader(lines[index:])
    if reader.fieldnames is None:
        raise SystemExit(f"{path}: ingen kolonneoverskrift fundet.")

    rows = [dict(row) for row in reader]
    return Manifest(comments=comments, fieldnames=list(reader.fieldnames), rows=rows)


def write_manifest(path: Path, manifest: Manifest) -> None:
    """Skriver atomisk: midlertidig fil i samme mappe, derefter os.replace."""
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8-sig",
        newline="",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            for comment in manifest.comments:
                handle.write(comment if comment.endswith("\n") else comment + "\n")
            writer = csv.DictWriter(handle, fieldnames=manifest.fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(manifest.rows)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Kørslen
# ---------------------------------------------------------------------------


@dataclass
class Change:
    row_index: int
    accession: str
    title: str
    authority: str
    document_type: str
    prescore: str
    old_decision: str
    new_decision: str
    verdict: Verdict


def build_changes(
    manifest: Manifest,
    *,
    target_decisions: set[str],
    tier_decisions: dict[str, str],
) -> list[Change]:
    changes: list[Change] = []
    for index, row in enumerate(manifest.rows):
        if (row.get("decision") or "").strip().lower() not in target_decisions:
            continue

        verdict = classify(
            row.get("title", ""),
            row.get("authority", ""),
            row.get("document_type", ""),
        )
        new_decision = tier_decisions.get(verdict.tier, row.get("decision", ""))
        changes.append(
            Change(
                row_index=index,
                accession=row.get("accession_number", ""),
                title=row.get("title", ""),
                authority=row.get("authority", ""),
                document_type=row.get("document_type", ""),
                prescore=row.get("prescore", ""),
                old_decision=row.get("decision", ""),
                new_decision=new_decision,
                verdict=verdict,
            )
        )
    return changes


def write_report(path: Path, changes: Iterable[Change]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "accession_number",
                "prescore",
                "authority",
                "document_type",
                "old_decision",
                "new_decision",
                "maritime_tier",
                "rule",
                "reason",
                "title",
            ]
        )
        for change in changes:
            writer.writerow(
                [
                    change.accession,
                    change.prescore,
                    change.authority,
                    change.document_type,
                    change.old_decision,
                    change.new_decision,
                    change.verdict.tier,
                    change.verdict.rule,
                    change.verdict.reason,
                    change.title,
                ]
            )


def verify_untouched(before: list[dict[str, str]], after: list[dict[str, str]], targets: set[str]) -> None:
    """Sikrer at kun de tilsigtede rækker ændrede decision, og intet andet."""
    if len(before) != len(after):
        raise SystemExit("AFBRUDT: antal rækker ændrede sig.")

    for index, (old, new) in enumerate(zip(before, after)):
        for key in old:
            if key == "decision":
                continue
            if old.get(key) != new.get(key):
                raise SystemExit(
                    f"AFBRUDT: række {index} fik ændret kolonnen '{key}'. Intet er skrevet."
                )
        if old.get("decision") != new.get("decision"):
            if (old.get("decision") or "").strip().lower() not in targets:
                raise SystemExit(
                    f"AFBRUDT: række {index} havde decision="
                    f"{old.get('decision')!r} og skulle ikke røres."
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regelbaseret oprydning i review-beslutninger i et discovery-manifest.",
    )
    parser.add_argument("manifest", type=Path, help="Sti til discovery-CSV'en.")
    parser.add_argument(
        "--only-decision",
        action="append",
        default=None,
        metavar="VÆRDI",
        help="Hvilke decision-værdier der må ændres (standard: review). Kan gentages.",
    )
    parser.add_argument(
        "--support-scheme-decision",
        default="include",
        choices=["include", "review", "exclude"],
        help="Beslutning for tilskuds- og støtteordninger (standard: include).",
    )
    parser.add_argument(
        "--fishery-decision",
        default="include",
        choices=["include", "review", "exclude"],
        help="Beslutning for fiskeriregulering (standard: include).",
    )
    parser.add_argument(
        "--housekeeping-decision",
        default="review",
        choices=["include", "review", "exclude"],
        help="Beslutning for administrativ oprydning (standard: review = uændret).",
    )
    parser.add_argument("--report", type=Path, default=None, help="Sti til rapport-CSV.")
    parser.add_argument("--apply", action="store_true", help="Skriv ændringerne i manifestet.")
    parser.add_argument("--no-backup", action="store_true", help="Undlad .bak-kopi.")
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="Tilføj kolonnerne maritime_tier og triage_reason i manifestet (fra som standard, "
        "fordi read_manifest() forventer et fast kolonnesæt).",
    )
    args = parser.parse_args(argv)

    if not args.manifest.exists():
        parser.error(f"Manifestet findes ikke: {args.manifest}")

    targets = {value.strip().lower() for value in (args.only_decision or ["review"])}
    tier_decisions = {
        "core": "include",
        "fishery": args.fishery_decision,
        "support": args.support_scheme_decision,
        "housekeeping": args.housekeeping_decision,
        "exclude": "exclude",
        "unresolved": "review",
    }

    manifest = read_manifest(args.manifest)
    before = [dict(row) for row in manifest.rows]

    decisions_before = Counter((row.get("decision") or "").strip() for row in manifest.rows)
    changes = build_changes(manifest, target_decisions=targets, tier_decisions=tier_decisions)

    if not changes:
        print(f"Ingen rækker med decision i {sorted(targets)}. Intet at gøre.")
        return 0

    # Anvend i hukommelsen.
    for change in changes:
        row = manifest.rows[change.row_index]
        row["decision"] = change.new_decision
        if args.annotate:
            row["maritime_tier"] = change.verdict.tier
            row["triage_reason"] = change.verdict.reason

    if args.annotate:
        for column in ("maritime_tier", "triage_reason"):
            if column not in manifest.fieldnames:
                manifest.fieldnames.append(column)
        for row in manifest.rows:
            row.setdefault("maritime_tier", "")
            row.setdefault("triage_reason", "")

    verify_untouched(before, manifest.rows, targets)

    # -- Rapportering ------------------------------------------------------
    by_tier = Counter(change.verdict.tier for change in changes)
    decisions_after = Counter((row.get("decision") or "").strip() for row in manifest.rows)

    print(f"Manifest      : {args.manifest}")
    print(f"Rækker i alt  : {len(manifest.rows)}")
    print(f"Behandlet     : {len(changes)} (decision i {sorted(targets)})")
    print()
    print("Tildelt kategori:")
    for tier, count in by_tier.most_common():
        target = tier_decisions.get(tier, "?")
        print(f"  {TIER_LABELS.get(tier, tier):<24} {count:>4}  -> decision={target}")

    print()
    print("Beslutninger før -> efter:")
    for value in sorted(set(decisions_before) | set(decisions_after)):
        before_count = decisions_before.get(value, 0)
        after_count = decisions_after.get(value, 0)
        arrow = "" if before_count == after_count else f"   ({after_count - before_count:+d})"
        print(f"  {value or '(tom)':<12} {before_count:>6} -> {after_count:>6}{arrow}")

    unresolved = [c for c in changes if c.verdict.tier == "unresolved"]
    if unresolved:
        print()
        print(f"Uafklaret ({len(unresolved)}) — forbliver review:")
        for change in unresolved:
            print(f"  {change.accession}  {change.title[:88]}")

    report_path = args.report or args.manifest.with_name(
        f"{args.manifest.stem}-triage-report.csv"
    )
    write_report(report_path, changes)
    print()
    print(f"Rapport skrevet: {report_path}")

    if not args.apply:
        print()
        print("TØRLØB — manifestet er ikke ændret. Kør igen med --apply for at skrive.")
        return 0

    if not args.no_backup:
        backup = args.manifest.with_suffix(args.manifest.suffix + ".bak")
        shutil.copy2(args.manifest, backup)
        print(f"Sikkerhedskopi : {backup}")

    write_manifest(args.manifest, manifest)

    # Læs filen igen og kontrollér at resultatet på disken er det forventede.
    reread = read_manifest(args.manifest)
    verify_untouched(before, reread.rows, targets)
    print(f"Manifest skrevet: {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Kommandolinjegrænseflade.

Kør fra `backend/`::

    python -m app.cli migrate                  # kør migrationer
    python -m app.cli seed                     # seed kategorier
    python -m app.cli import --source fixture  # kør import
    python -m app.cli import --source fixture --fixture-revision 2
    python -m app.cli import --source production --since 2026-08-01
    python -m app.cli classify "Bekendtgørelse om sikkerhed på passagerskibe"
    python -m app.cli stats

Historisk efterindlæsning (accessionsnumre uden om ændringsfeeden)::

    python -m app.cli backfill probe-search --out probe.json
    python -m app.cli backfill discover --out manifests/soefartsstyrelsen.csv
    python -m app.cli backfill enqueue-manifest \\
        --file manifests/soefartsstyrelsen.csv --tag soefartsstyrelsen-historical-2026
    python -m app.cli backfill enqueue --file accessions.txt --tag sofart-2024
    python -m app.cli backfill enqueue --id B20220122005 --id B20190094605
    python -m app.cli backfill run --source production --batch-size 25
    python -m app.cli backfill status

`discover` lægger ALDRIG noget i køen. Den skriver en CSV, som gennemgås,
før `enqueue-manifest` kaldes.

Importen kan også startes via API'et: POST /api/import/run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.migrations_runner import run_migrations
from app.db.seed import seed_categories
from app.db.session import session_scope
from app.models import Document, DocumentVersion, ImportRun
from app.services.backfill import manifest, run_backfill
from app.services.categorization import get_categorization_engine
from app.services.discovery import (
    DEFAULT_DECISION,
    DiscoveryError,
    DiscoveryGroup,
    DiscoveryQuery,
    DiscoveryService,
    build_discovery_client,
    read_manifest,
)
from app.services.discovery.extract import describe_payload
from app.services.discovery.search_client import RetsinformationSearchClient
from app.services.discovery.service import VERIFIED_COUNTS
from app.services.importer import ImportService
from app.services.relevance import get_relevance_engine
from app.services.retsinformation import build_source_client
from app.services.retsinformation.base import NormalizedDocument

logger = get_logger(__name__)


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Ugyldig dato {value!r}. Brug formatet ÅÅÅÅ-MM-DD."
        ) from exc


# ---------------------------------------------------------------------------
# Kommandoer
# ---------------------------------------------------------------------------


def cmd_migrate(_: argparse.Namespace) -> int:
    run_migrations()
    print("Migrationer kørt. Databasen er på nyeste revision.")
    return 0


def cmd_seed(_: argparse.Namespace) -> int:
    with session_scope() as session:
        count = seed_categories(session)
    print(f"{count} kategorier seedet.")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Kører en import. Kilden skal vælges bevidst."""
    client = build_source_client(args.source, fixture_revision=args.fixture_revision)

    if getattr(client, "kind", "") == "fixture":
        print(
            "ADVARSEL: importerer SYNTETISKE testdata. Disse dokumenter er ikke\n"
            "         hentet fra Retsinformation og er ikke gældende ret.\n"
        )

    try:
        with session_scope() as session:
            service = ImportService(
                session,
                client=client,
                relevance_engine=get_relevance_engine(),
                categorization_engine=get_categorization_engine(),
            )
            summary = service.run(since=args.since, trigger="cli", limit=args.limit)
    finally:
        client.close()

    print(
        f"\nImport #{summary.import_run_id} — {summary.status}\n"
        f"  Kontrolleret : {summary.checked}\n"
        f"  Oprettet     : {summary.created}\n"
        f"  Opdateret    : {summary.updated}\n"
        f"  Uændret      : {summary.unchanged}\n"
        f"  Afvist       : {summary.rejected}  (ikke maritimt relevante)\n"
        f"  Fejlet       : {summary.failed}"
    )
    for error in summary.errors[:10]:
        print(f"    ! {error['source_id']}: {error['error_type']} — {error['error'][:120]}")

    return 0 if summary.status != "FAILED" else 1


# -- Efterindlæsning --------------------------------------------------------


def _read_accessions(args: argparse.Namespace) -> list[str]:
    """Samler accessionsnumre fra --id og --file."""
    accessions: list[str] = list(args.id or [])

    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"Filen findes ikke: {path}", file=sys.stderr)
            return []
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.split("#", 1)[0].strip()
            if value:
                accessions.append(value)

    return list(dict.fromkeys(accessions))


def cmd_backfill_enqueue(args: argparse.Namespace) -> int:
    accessions = _read_accessions(args)
    if not accessions:
        print("Ingen accessionsnumre angivet. Brug --id og/eller --file.", file=sys.stderr)
        return 2

    with session_scope() as session:
        counts = manifest.enqueue(
            session,
            accessions,
            source_tag=args.tag,
            priority=args.priority,
            requeue_terminal=args.requeue_failed,
        )

    print(
        f"Kø opdateret ({args.tag}):\n"
        f"  Tilføjet     : {counts['added']}\n"
        f"  Genindsat    : {counts['requeued']}\n"
        f"  Sprunget over: {counts['skipped']}  (findes allerede)"
    )
    return 0


def cmd_backfill_probe_search(args: argparse.Namespace) -> int:
    """Ét enkelt søgekald, gemt råt og beskrevet strukturelt.

    Formålet er den forundersøgelse, der skal ligge før discovery tages i
    brug: indeholder svaret accessionsnumre, hvordan pagineres der, og er
    formatet stabilt nok? Kommandoen henter præcis én side og skriver
    intet i databasen.
    """
    try:
        client = RetsinformationSearchClient(
            url=args.url, method=args.method, page_size=args.page_size
        )
    except DiscoveryError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    query = DiscoveryQuery(authority=args.authority, status=args.status, label="probe")

    try:
        payload, sent = client.request_page(query, page=client.first_page, offset=0)
    except DiscoveryError as exc:
        print(f"\nSøgekaldet mislykkedes:\n  {exc}\n", file=sys.stderr)
        return 1
    finally:
        client.close()

    description = describe_payload(payload)

    print(f"\nURL      : {client.method} {client.url}")
    print(f"Sendt    : {json.dumps(sent, ensure_ascii=False)}")
    print(f"Svartype : {description['toplevel_type']}")
    if description["toplevel_keys"]:
        print(f"Topnøgler: {', '.join(description['toplevel_keys'])}")
    print(f"Poster   : {description['records_found']}")
    print(f"Kildens samlede antal : {description['reported_total']}")
    print(f"Pagineringsnøgler     : {', '.join(description['pagination_keys']) or 'ingen fundet'}")
    if description["record_keys"]:
        print(f"Feltnavne i en post   : {', '.join(description['record_keys'])}")
    print(f"Accessionsnumre       : {', '.join(str(a) for a in description['accession_numbers'])}")
    if description["sample_extraction"]:
        print("Udtræk af første post :")
        for key, value in description["sample_extraction"].items():
            print(f"    {key:18s}: {value}")

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nRåt svar gemt: {target}")

    if not description["records_found"]:
        print(
            "\nIngen poster genkendt. Enten gav søgningen intet, eller svaret har "
            "en struktur udtrækket ikke genkender. Se den gemte JSON.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_backfill_discover(args: argparse.Namespace) -> int:
    """Finder kandidat-accessionsnumre og skriver et CSV-manifest.

    Lægger bevidst intet i køen.
    """
    settings = get_settings()
    source = (args.source or settings.source_client or "").strip().lower()

    try:
        client = build_discovery_client(source)
    except (DiscoveryError, ValueError) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    if getattr(client, "kind", "") == "fixture":
        print(
            "ADVARSEL: opdager i SYNTETISKE testdata. De fundne accessionsnumre\n"
            "         findes ikke hos Retsinformation.\n"
        )

    # Fixturkørsler har naturligvis ikke de verificerede tal.
    default_current = 0 if source == "fixture" else VERIFIED_COUNTS["gældende"]
    default_historical = 0 if source == "fixture" else VERIFIED_COUNTS["historisk"]
    expect_current = default_current if args.expect_current is None else args.expect_current
    expect_historical = (
        default_historical if args.expect_historical is None else args.expect_historical
    )

    groups = [
        DiscoveryGroup(label="gældende", status=args.status_current,
                       expected=expect_current or None),
        DiscoveryGroup(label="historisk", status=args.status_historical,
                       expected=expect_historical or None),
    ]
    expected_total = (expect_current + expect_historical) or None

    output = Path(args.out) if args.out else settings.manifest_dir / "discovery.csv"

    service = DiscoveryService(client)
    try:
        report = service.discover(
            authority=args.authority,
            groups=groups,
            expected_total=expected_total,
            output_path=None if args.dry_run else output,
            decision=args.decision,
            allow_count_mismatch=args.allow_count_mismatch,
        )
    except DiscoveryError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1
    finally:
        client.close()

    print(f"\nOpdagelse — {args.authority} ({service.client_kind})")
    for outcome in report.outcomes:
        expected = outcome.group.expected
        expected_text = f" (forventet {expected})" if expected else ""
        print(
            f"  {outcome.group.label:10s}: {outcome.found:6d}{expected_text}"
            f"  sider={outcome.pages}  kildens tal={outcome.reported_total}"
        )
    print(f"  {'unikke':10s}: {report.total:6d}"
          + (f" (forventet {expected_total})" if expected_total else ""))
    if report.duplicates:
        print(f"  dubletter fjernet: {report.duplicates}")

    for problem in report.problems:
        print(f"  ! {problem}")

    if args.dry_run:
        print("\nPrøvekørsel — intet manifest skrevet.")
    elif report.manifest_path:
        print(f"\nManifest skrevet: {report.manifest_path}")
        print(
            "Gennemgå filen (kolonnen 'decision'), og læg den derefter i kø med:\n"
            f"  python -m app.cli backfill enqueue-manifest --file {report.manifest_path} "
            "--tag <mærkat>"
        )

    print("\nIntet er lagt i køen. Det sker først med enqueue-manifest.")
    return 0 if report.ok else 1


def cmd_backfill_enqueue_manifest(args: argparse.Namespace) -> int:
    """Lægger de godkendte linjer fra et CSV-manifest i køen."""
    try:
        rows = read_manifest(args.file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    wanted = {value.strip().casefold() for value in (args.decision or [DEFAULT_DECISION])}
    selected = [row.accession_number for row in rows if row.decision in wanted]
    skipped = len(rows) - len(selected)

    print(
        f"Manifest {args.file}:\n"
        f"  Linjer i alt : {len(rows)}\n"
        f"  Valgt        : {len(selected)}  (decision i {', '.join(sorted(wanted))})\n"
        f"  Fravalgt     : {skipped}"
    )

    if not selected:
        print("Ingen linjer at lægge i kø.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\nPrøvekørsel — intet lagt i kø.")
        for accession_number in selected[:20]:
            print(f"  {accession_number}")
        if len(selected) > 20:
            print(f"  ... og {len(selected) - 20} mere")
        return 0

    with session_scope() as session:
        counts = manifest.enqueue(
            session,
            selected,
            source_tag=args.tag,
            priority=args.priority,
            requeue_terminal=args.requeue_failed,
        )

    print(
        f"\nKø opdateret ({args.tag}):\n"
        f"  Tilføjet     : {counts['added']}\n"
        f"  Genindsat    : {counts['requeued']}\n"
        f"  Sprunget over: {counts['skipped']}  (findes allerede)"
    )
    return 0


def cmd_backfill_run(args: argparse.Namespace) -> int:
    """Kører køen igennem. Kilden skal vælges eksplicit."""
    client = build_source_client(args.source, fixture_revision=args.fixture_revision)

    if getattr(client, "kind", "") == "fixture":
        print(
            "ADVARSEL: efterindlæser fra SYNTETISKE testdata. Disse dokumenter\n"
            "         er ikke hentet fra Retsinformation og er ikke gældende ret.\n"
        )

    try:
        result = run_backfill(
            client=client,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            max_attempts=args.max_attempts,
            lease_minutes=args.lease_minutes,
        )
    finally:
        client.close()

    print(
        f"\nEfterindlæsning færdig — arbejder {result.worker_id}\n"
        f"  Portioner    : {result.batches}\n"
        f"  Reserveret   : {result.claimed}\n"
        f"  Gennemført   : {result.completed}\n"
        f"  Afvist       : {result.rejected}  (ikke maritimt relevante)\n"
        f"  Nyt forsøg   : {result.retry}\n"
        f"  Opgivet      : {result.failed}\n"
        f"  Frigivet     : {result.released}  (kørsel afbrudt før behandling)\n"
        f"  Tabte leases : {result.fence_breaches}"
    )
    if result.import_run_ids:
        ids = ", ".join(f"#{i}" for i in result.import_run_ids)
        print(f"  Importkørsler: {ids}")

    if result.stopped_early:
        print(
            f"\nSTOPPET FØR KØEN VAR TOM: {result.stopped_early}.\n"
            "Kilden ser ud til at være utilgængelig. De reserverede poster er\n"
            "sat til nyt forsøg. Kør kommandoen igen, når kilden svarer."
        )
        return 1
    return 0


def cmd_backfill_status(args: argparse.Namespace) -> int:
    with session_scope() as session:
        # Alle tre opslag skal have samme afgrænsning, ellers viser
        # listerne poster fra andre manifests under en tagfiltreret
        # overskrift.
        counts = manifest.queue_counts(session, source_tag=args.tag)
        upcoming = list(
            manifest.pending_accessions(session, limit=args.show, source_tag=args.tag)
        )
        failures = list(
            manifest.failed_items(session, limit=args.show, source_tag=args.tag)
        )

    total = counts.pop("TOTAL", 0)
    print(f"Efterindlæsningskø{f' ({args.tag})' if args.tag else ''} — {total} poster")
    for status, count in counts.items():
        print(f"  {status:11s}: {count}")

    if upcoming:
        print("\nNæste i køen:")
        for accn in upcoming:
            print(f"  {accn}")

    if failures:
        print("\nOpgivet:")
        for item in failures:
            print(f"  {item.accession_number}  {(item.last_error or '')[:100]}")
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    """Vurderer en titel/tekst uden at gemme noget.

    Nyttigt når man justerer config/maritime_keywords.yaml.
    """
    document = NormalizedDocument(
        source="cli",
        source_id="cli",
        title=args.title,
        content=args.content or "",
        authority=args.authority,
    )
    result = get_relevance_engine().classify(document)

    print(f"\n{args.title}")
    print(f"  Score          : {result.score}/100  ({result.classification})")
    print(f"  Maritimt       : {'ja' if result.is_maritime else 'nej'}")
    print(f"  Begrundelse    : {result.reason}")
    calc = result.to_json()["calculation"]
    print(
        f"  Regnestykke    : positiv {calc['positive_raw']} "
        f"+ breddebonus {calc['concept_bonus']} "
        f"- negativ {calc['negative_raw']} = {calc['raw_score']} "
        f"-> normaliseret {calc['normalized_score']}"
    )
    if calc["title_floor_applied"]:
        print(f"  Titelautoritet : {', '.join(calc['title_floor_terms'])}")
    if result.matches:
        print("  Termbidrag:")
        for match in result.matches[:10]:
            capped = " (loft nået)" if match.occurrences > match.counted_occurrences else ""
            print(
                f"    {match.contribution:7.1f}  {match.term:22s} "
                f"{match.field:10s} x{match.occurrences}{capped}"
            )
    if result.negative_matches:
        print("  Negative signaler:")
        for match in result.negative_matches[:5]:
            print(f"   -{match.contribution:7.1f}  {match.term:22s} {match.field}")

    if result.is_maritime:
        categories = get_categorization_engine().categorize(document)
        print("  Kategorier:")
        for assignment in categories.assignments:
            print(f"    {assignment.confidence:.2f}  {assignment.name}")
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    with session_scope() as session:
        documents = session.scalar(select(func.count(Document.id))) or 0
        maritime = (
            session.scalar(
                select(func.count(Document.id)).where(Document.is_maritime.is_(True))
            )
            or 0
        )
        synthetic = (
            session.scalar(
                select(func.count(Document.id)).where(Document.is_synthetic.is_(True))
            )
            or 0
        )
        versions = session.scalar(select(func.count(DocumentVersion.id))) or 0
        last = session.scalars(
            select(ImportRun).order_by(ImportRun.started_at.desc()).limit(1)
        ).first()

    print(f"Dokumenter      : {documents}")
    print(f"  heraf maritime: {maritime}")
    print(f"  heraf syntetiske: {synthetic}")
    print(f"Versioner       : {versions}")
    if last:
        print(
            f"Seneste import  : #{last.id} {last.status} "
            f"({last.client_kind}) {last.started_at:%Y-%m-%d %H:%M}"
        )
    else:
        print("Seneste import  : ingen kørsler endnu")
    return 0


# ---------------------------------------------------------------------------
# Argumentparser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Maritim Lovdatabase — administrationskommandoer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Kør databasemigrationer.").set_defaults(func=cmd_migrate)
    sub.add_parser("seed", help="Seed maritime kategorier.").set_defaults(func=cmd_seed)
    sub.add_parser("stats", help="Vis nøgletal fra databasen.").set_defaults(func=cmd_stats)

    importer = sub.add_parser("import", help="Kør en import fra kilden.")
    importer.add_argument(
        "--source",
        choices=["fixture", "production"],
        default=None,
        help="Kilde. Standard er SOURCE_CLIENT fra konfigurationen. "
        "Der falder aldrig automatisk tilbage til fixture.",
    )
    importer.add_argument(
        "--fixture-revision", type=int, default=1, choices=[1, 2],
        help="Hvilket fixtursæt der indlæses (kun for --source fixture).",
    )
    importer.add_argument(
        "--since", type=_parse_date, default=None,
        help="Hent kun dokumenter ændret fra og med denne dato (ÅÅÅÅ-MM-DD).",
    )
    importer.add_argument("--limit", type=int, default=None, help="Behandl højst N dokumenter.")
    importer.set_defaults(func=cmd_import)

    # -- backfill ----------------------------------------------------------
    backfill = sub.add_parser(
        "backfill",
        help="Historisk efterindlæsning via accessionsnumre.",
        description="Ændringsfeeden rækker kun ti dage tilbage. Ældre dokumenter "
        "hentes ved at lægge deres accessionsnumre i en kø og køre den igennem. "
        "Rækkefølge: probe-search -> discover -> (gennemgang af CSV) -> "
        "enqueue-manifest -> run.",
    )
    backfill_sub = backfill.add_subparsers(dest="backfill_command", required=True)

    probe = backfill_sub.add_parser(
        "probe-search",
        help="Ét søgekald: gem det rå svar og beskriv strukturen.",
        description="Forundersøgelse af søgegrænsefladen. Henter én side og "
        "skriver hverken i database eller kø.",
    )
    probe.add_argument("--url", default=None, help="Standard: RETSINFORMATION_SEARCH_URL.")
    probe.add_argument("--method", default=None, choices=["GET", "POST"])
    probe.add_argument("--authority", default="Søfartsstyrelsen")
    probe.add_argument("--status", default=None, help="Kildens statusfilter, f.eks. Gældende.")
    probe.add_argument("--page-size", type=int, default=None)
    probe.add_argument("--out", default=None, help="Gem det rå JSON-svar her.")
    probe.set_defaults(func=cmd_backfill_probe_search)

    discover = backfill_sub.add_parser(
        "discover",
        help="Find kandidat-accessionsnumre og skriv et CSV-manifest.",
        description="Søger pr. myndighed, opdeler gældende og historiske, "
        "fjerner dubletter, kontrollerer tallene og skriver en CSV til "
        "gennemgang. Lægger ALDRIG noget i køen.",
    )
    discover.add_argument(
        "--source", choices=["fixture", "production"], default=None,
        help="Kilde. Standard er SOURCE_CLIENT fra konfigurationen.",
    )
    discover.add_argument("--authority", default="Søfartsstyrelsen", help="Myndighedsfilter.")
    discover.add_argument(
        "--status-current", default="Gældende", help="Kildens værdi for gældende dokumenter."
    )
    discover.add_argument(
        "--status-historical", default="Historisk", help="Kildens værdi for historiske."
    )
    discover.add_argument(
        "--expect-current", type=int, default=None,
        help=f"Forventet antal gældende. Standard: {VERIFIED_COUNTS['gældende']}. 0 slår kontrollen fra.",
    )
    discover.add_argument(
        "--expect-historical", type=int, default=None,
        help=f"Forventet antal historiske. Standard: {VERIFIED_COUNTS['historisk']}. 0 slår kontrollen fra.",
    )
    discover.add_argument(
        "--allow-count-mismatch", action="store_true",
        help="Skriv manifestet selvom tallene afviger. Filen mærkes med en advarsel.",
    )
    discover.add_argument(
        "--decision", default=DEFAULT_DECISION,
        help=f"Startværdi i CSV'ens decision-kolonne. Standard: {DEFAULT_DECISION}.",
    )
    discover.add_argument("--out", default=None, help="Sti til CSV-manifestet.")
    discover.add_argument(
        "--dry-run", action="store_true", help="Kør søgningen uden at skrive CSV."
    )
    discover.set_defaults(func=cmd_backfill_discover)

    enqueue_manifest = backfill_sub.add_parser(
        "enqueue-manifest",
        help="Læg de godkendte linjer fra et CSV-manifest i køen.",
        description="Læser CSV'en fra discover og lægger de linjer i kø, hvis "
        "decision-kolonne er godkendt.",
    )
    enqueue_manifest.add_argument("--file", required=True, help="Sti til CSV-manifestet.")
    enqueue_manifest.add_argument("--tag", required=True, help="Mærkat for køposterne.")
    enqueue_manifest.add_argument(
        "--decision", action="append", default=None,
        help=f"Godkendte værdier i decision-kolonnen. Standard: {DEFAULT_DECISION}.",
    )
    enqueue_manifest.add_argument("--priority", type=int, default=100)
    enqueue_manifest.add_argument(
        "--requeue-failed", action="store_true",
        help="Nulstil poster der tidligere blev opgivet.",
    )
    enqueue_manifest.add_argument(
        "--dry-run", action="store_true", help="Vis hvad der ville blive lagt i kø."
    )
    enqueue_manifest.set_defaults(func=cmd_backfill_enqueue_manifest)

    enqueue = backfill_sub.add_parser("enqueue", help="Læg accessionsnumre i køen.")
    enqueue.add_argument(
        "--id", action="append", default=[], metavar="ACCN",
        help="Accessionsnummer. Kan gentages.",
    )
    enqueue.add_argument(
        "--file", default=None,
        help="Fil med ét accessionsnummer pr. linje. '#' starter en kommentar.",
    )
    enqueue.add_argument(
        "--tag", default="manual",
        help="Mærkat for hvor listen kommer fra. Standard: manual.",
    )
    enqueue.add_argument(
        "--priority", type=int, default=100, help="Lavere tal behandles først."
    )
    enqueue.add_argument(
        "--requeue-failed", action="store_true",
        help="Nulstil poster der tidligere blev opgivet.",
    )
    enqueue.set_defaults(func=cmd_backfill_enqueue)

    run_queue = backfill_sub.add_parser("run", help="Kør køen igennem.")
    run_queue.add_argument(
        "--source", choices=["fixture", "production"], default=None,
        help="Kilde. Standard er SOURCE_CLIENT fra konfigurationen.",
    )
    run_queue.add_argument(
        "--fixture-revision", type=int, default=1, choices=[1, 2],
        help="Kun for --source fixture.",
    )
    run_queue.add_argument(
        "--batch-size", type=int, default=25,
        help="Accessionsnumre pr. importkørsel. Standard: 25.",
    )
    run_queue.add_argument(
        "--max-batches", type=int, default=None,
        help="Stop efter N portioner. Standard: tøm køen.",
    )
    run_queue.add_argument(
        "--max-attempts", type=int, default=manifest.DEFAULT_MAX_ATTEMPTS,
        help="Forsøg pr. post før den opgives.",
    )
    run_queue.add_argument(
        "--lease-minutes", type=int, default=manifest.DEFAULT_LEASE_MINUTES,
        help="Reservationens levetid. Skal overstige den langsomste hentning.",
    )
    run_queue.set_defaults(func=cmd_backfill_run)

    queue_status = backfill_sub.add_parser("status", help="Vis køens tilstand.")
    queue_status.add_argument("--tag", default=None, help="Filtrér på mærkat.")
    queue_status.add_argument(
        "--show", type=int, default=10, help="Antal poster der vises pr. liste."
    )
    queue_status.set_defaults(func=cmd_backfill_status)

    classify = sub.add_parser("classify", help="Test relevansvurdering af en titel.")
    classify.add_argument("title", help="Dokumenttitel.")
    classify.add_argument("--content", default=None, help="Valgfri brødtekst.")
    classify.add_argument("--authority", default=None, help="Valgfri myndighed.")
    classify.set_defaults(func=cmd_classify)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging(get_settings().log_level)
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

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

    python -m app.cli backfill enqueue --file accessions.txt --tag sofart-2024
    python -m app.cli backfill enqueue --id B20220122005 --id B20190094605
    python -m app.cli backfill run --source production --batch-size 25
    python -m app.cli backfill status

Importen kan også startes via API'et: POST /api/import/run.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.migrations_runner import run_migrations
from app.db.seed import seed_categories
from app.db.session import session_scope
from app.models import BackfillManifestItem, Document, DocumentVersion, ImportRun
from app.services.backfill import manifest, run_backfill
from app.services.categorization import get_categorization_engine
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
    return 0


def cmd_backfill_status(args: argparse.Namespace) -> int:
    with session_scope() as session:
        counts = manifest.queue_counts(session, source_tag=args.tag)
        upcoming = list(manifest.pending_accessions(session, limit=args.show))
        failures = session.scalars(
            select(BackfillManifestItem)
            .where(BackfillManifestItem.status == "FAILED")
            .order_by(BackfillManifestItem.updated_at.desc())
            .limit(args.show)
        ).all()

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
        "hentes ved at lægge deres accessionsnumre i en kø og køre den igennem.",
    )
    backfill_sub = backfill.add_subparsers(dest="backfill_command", required=True)

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

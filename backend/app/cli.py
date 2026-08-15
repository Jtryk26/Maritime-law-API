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
    python -m app.cli backfill discover-global --out manifests/discovery-global.csv
    python -m app.cli backfill enqueue-manifest \\
        --file manifests/soefartsstyrelsen.csv --tag soefartsstyrelsen-historical-2026
    python -m app.cli backfill enqueue --file accessions.txt --tag sofart-2024
    python -m app.cli backfill enqueue --id B20220122005 --id B20190094605
    python -m app.cli backfill run --source production --batch-size 25
    python -m app.cli backfill status

`discover` lægger ALDRIG noget i køen. Den skriver en CSV, som gennemgås,
før `enqueue-manifest` kaldes.

Kurateret relevans-override (menneskelig rettelse af den automatiske
relevansvurdering, for netop de angivne accessionsnumre)::

    python -m app.cli backfill enqueue --file curated.txt \\
        --tag curated-review-2026-08 \\
        --curated-include --curated-reason "Kontrolleret manuelt: maritim" \\
        --requeue-rejected
    # Allerede importeret dokument, der skal ekskluderes:
    python -m app.cli backfill enqueue --id C20190977160 \\
        --tag curated-review-2026-08 \\
        --curated-exclude --curated-reason "Generel regulering, ikke maritim" \\
        --requeue-completed
    python -m app.cli backfill curated-status
    python -m app.cli backfill curated-history --accession C20190977160

Semantisk indeks (vektorer). Køres EFTER import, aldrig under den::

    python -m app.cli embed model-info      # bekræft model og dimension
    python -m app.cli embed run             # vektorisér det der mangler
    python -m app.cli embed run --reset     # byg forfra efter modelskifte
    python -m app.cli embed status          # dækning og tilstand
    python -m app.cli search-log --without-results

Importen kan også startes via API'et: POST /api/import/run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select, text as sql_text

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.migrations_runner import run_migrations
from app.db.seed import seed_categories
from app.db.session import session_scope
from app.db.vector_support import reset_vector_support_cache, vector_column_dimensions
from app.models import BackfillManifestItem, CuratedDecision, Document, DocumentVersion, ImportRun
from app.services.backfill import manifest, run_backfill
from app.services.categorization import get_categorization_engine
from app.services.curation import bulk_set_overrides, list_overrides, override_history
from app.services.embedding import EmbeddingIndexer, get_embedding_provider
from app.services.discovery import (
    DEFAULT_DECISION,
    DiscoveryError,
    DiscoveryGroup,
    DiscoveryQuery,
    DiscoveryService,
    build_discovery_client,
    discover_global,
    load_global_config,
    read_manifest,
    write_global_manifest,
)
from app.services.discovery.global_service import GlobalDiscoveryConfig
from app.services.discovery.extract import describe_payload
from app.services.discovery.search_client import RetsinformationSearchClient
from app.services.discovery.service import VERIFIED_COUNTS
from app.services.search import QueryLogService
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

    curated_decision = None
    if args.curated_include:
        curated_decision = CuratedDecision.INCLUDE.value
    elif args.curated_exclude:
        curated_decision = CuratedDecision.EXCLUDE.value

    if curated_decision and not (args.curated_reason and args.curated_reason.strip()):
        print(
            "--curated-reason er påkrævet sammen med "
            "--curated-include/--curated-exclude.",
            file=sys.stderr,
        )
        return 2
    if args.curated_reason and not curated_decision:
        print(
            "--curated-reason kræver --curated-include eller --curated-exclude.",
            file=sys.stderr,
        )
        return 2
    # --requeue-completed genimporterer dokumenter, der allerede er hentet
    # og gemt uden problemer. Den eneste legitime grund til det er, at en
    # kurateret afgørelse er kommet til EFTER importen og ellers først ville
    # slå igennem ved en tilfældig senere genimport. Uden en curated
    # beslutning er flaget derfor ren risiko uden gevinst, og afvises.
    if args.requeue_completed and not curated_decision:
        print(
            "--requeue-completed er kun tilladt sammen med --curated-include "
            "eller --curated-exclude. Uden en kurateret beslutning ville det "
            "blot genimportere allerede færdigbehandlede dokumenter.",
            file=sys.stderr,
        )
        return 2

    with session_scope() as session:
        if curated_decision:
            override_counts = bulk_set_overrides(
                session,
                accessions,
                curated_decision,
                reason=args.curated_reason,
                source_tag=args.tag,
                decided_by=args.decided_by,
            )
            print(
                f"Curated override registreret ({curated_decision}, {len(accessions)} numre):\n"
                f"  Nye          : {override_counts['created']}\n"
                f"  Ændret       : {override_counts['updated']}\n"
                f"  Uændret      : {override_counts['unchanged']}  "
                f"(identisk afgørelse — ingen historikpost skrevet)\n"
            )

        counts = manifest.enqueue(
            session,
            accessions,
            source_tag=args.tag,
            priority=args.priority,
            requeue_terminal=args.requeue_failed,
            requeue_rejected=args.requeue_rejected,
            requeue_completed=args.requeue_completed,
        )

    print(
        f"Kø opdateret ({args.tag}):\n"
        f"  Tilføjet     : {counts['added']}\n"
        f"  Genindsat    : {counts['requeued']}\n"
        f"  Sprunget over: {counts['skipped']}  (findes allerede)"
    )
    return 0


def cmd_backfill_curated_status(args: argparse.Namespace) -> int:
    """Viser registrerede kuraterede overrides. Kun til visning."""
    with session_scope() as session:
        rows = list_overrides(session, decision=args.decision, source_tag=args.tag)

    if not rows:
        print("Ingen kuraterede overrides fundet.")
        return 0

    print(f"Kuraterede overrides — {len(rows)} i alt")
    for row in rows[: args.show]:
        print(
            f"  {row.accession_number:16s} {row.decision:8s} "
            f"[{row.source_tag}]  {row.reason[:80]}"
        )
    if len(rows) > args.show:
        print(f"  ... og {len(rows) - args.show} mere")
    return 0


def cmd_backfill_curated_history(args: argparse.Namespace) -> int:
    """Viser den append-only historik for kuraterede afgørelser.

    Historikken omfatter også accessionsnumre, hvis override siden er
    fjernet — de fremgår ikke af `curated-status`.
    """
    with session_scope() as session:
        events = override_history(session, args.accession)

    if not events:
        print("Ingen historik fundet.")
        return 0

    scope = f" for {args.accession}" if args.accession else ""
    print(f"Kurateringshistorik{scope} — {len(events)} hændelse(r), ældste først")
    for event in events[: args.show]:
        stamp = event.created_at.isoformat(timespec="seconds") if event.created_at else "?"
        transition = f"{event.previous_decision or '-'} -> {event.new_decision or '(fjernet)'}"
        print(
            f"  {stamp}  {event.accession_number:16s} "
            f"{event.event_type:17s} {transition}"
        )
        reason = event.new_reason or event.previous_reason or ""
        if reason:
            print(f"      begrundelse: {reason[:100]}")
        if event.new_source_tag or event.previous_source_tag:
            print(
                f"      tag: {event.previous_source_tag or '-'} -> "
                f"{event.new_source_tag or '-'}"
            )
    if len(events) > args.show:
        print(f"  ... og {len(events) - args.show} mere")
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


def _known_accessions(session) -> frozenset[str]:
    """Accessionsnumre der allerede er i køen (uanset status) eller gemt.

    Bruges til at mærke `discover-global`-fund som allerede kendte, så de
    2.888 Søfartsstyrelsen-dokumenter (eller noget som helst andet, der
    allerede er behandlet) aldrig lægges i kø igen ved et uheld — CSV'en
    viser dem stadig, blot mærket, i stedet for at skjule dem.
    """
    from_queue = set(session.scalars(select(BackfillManifestItem.accession_number)).all())
    from_documents = set(
        session.scalars(
            select(Document.source_id).where(Document.source == "retsinformation")
        ).all()
    )
    return frozenset(from_queue | from_documents)


def cmd_backfill_discover_global(args: argparse.Namespace) -> int:
    """Finder kandidat-accessionsnumre på tværs af myndigheder.

    Bruger samme søgemekanisme som `discover`, men looper over en liste af
    myndigheder fra `config/discovery_global.yaml` og forhåndsvurderer hvert
    fund med relevansmotoren. Lægger bevidst intet i køen — se
    `enqueue-manifest`. Den eksisterende, verificerede Søfartsstyrelsen-
    opdagelse (`backfill discover`) berøres ikke af denne kommando.
    """
    settings = get_settings()
    source = (args.source or settings.source_client or "").strip().lower()

    config_path = Path(args.config) if args.config else settings.discovery_global_config_path
    try:
        global_config = load_global_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    if args.authority:
        wanted = {a.strip() for a in args.authority}
        authorities = [a for a in global_config.authorities if a in wanted]
        if not authorities:
            print(
                f"Ingen af de angivne --authority findes i {config_path}.",
                file=sys.stderr,
            )
            return 2
        global_config = GlobalDiscoveryConfig(
            authorities=tuple(authorities),
            deny_title_patterns=global_config.deny_title_patterns,
        )

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

    with session_scope() as session:
        known = _known_accessions(session)

    print(
        f"Global opdagelse — {len(global_config.authorities)} myndigheder "
        f"({client.kind}), {len(known)} accessionsnumre allerede kendt.\n"
    )

    try:
        report = discover_global(
            client,
            config=global_config,
            relevance_engine=get_relevance_engine(),
            status_current=args.status_current,
            status_historical=args.status_historical,
            known_accessions=known,
        )
    except DiscoveryError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1
    finally:
        client.close()

    for outcome in report.outcomes:
        print(f"  {outcome.authority:35s}: {outcome.found:6d}  nye={outcome.new:<6d}")
        for problem in outcome.problems:
            print(f"      ! {problem}")

    counts = report.decision_counts
    print(
        f"\nI alt {report.total} unikke kandidater "
        f"(dubletter på tværs af myndigheder: {report.duplicates})"
    )
    print(
        f"  include={counts['include']}  review={counts['review']}  "
        f"exclude={counts['exclude']}  skip(allerede kendt)={counts['skip']}"
    )

    if args.dry_run:
        print("\nPrøvekørsel — intet manifest skrevet.")
        return 0

    output = Path(args.out) if args.out else settings.manifest_dir / "discovery-global.csv"
    comment = None
    if client.kind == "fixture":
        comment = "SYNTETISKE DATA — konstrueret til test. Ikke hentet fra Retsinformation."
    write_global_manifest(output, report.hits, header_comment=comment)

    print(f"\nManifest skrevet: {output}")
    print(
        "Gennemgå filen (kolonnen 'decision' — kun 'include' er forhåndsudvalgt).\n"
        "Læg de godkendte linjer i kø med:\n"
        f"  python -m app.cli backfill enqueue-manifest --file {output} --tag <mærkat>"
    )
    print("\nIntet er lagt i køen. Det sker først med enqueue-manifest.")
    return 0


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
            requeue_rejected=args.requeue_rejected,
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
# Semantisk indeks
# ---------------------------------------------------------------------------


def _embedding_provider_or_exit():
    """Henter udbyderen eller forklarer hvorfor den ikke kan skaffes."""
    settings = get_settings()
    if not settings.embeddings_enabled:
        print("Vektorlaget er slået fra (EMBEDDINGS_ENABLED=false).")
        return None
    try:
        return get_embedding_provider()
    except Exception as exc:  # noqa: BLE001
        print(f"Embedding-modellen kunne ikke indlæses: {exc}")
        return None


def cmd_embed_run(args: argparse.Namespace) -> int:
    """Vektoriserer de dokumenter hvis vektorer mangler eller er forældede."""
    provider = _embedding_provider_or_exit()
    if provider is None:
        return 2

    if not provider.info.semantic:
        # Hash-udbyderen må gerne bruges, men aldrig ubemærket: den
        # finder ikke synonymer, og et indeks bygget med den vil skuffe
        # enhver der tror det er betydningssøgning.
        print(
            "ADVARSEL: udbyderen "
            f"{provider.info.provider!r} er ikke semantisk. "
            "Indekset vil ikke kunne finde beslægtede formuleringer."
        )

    with session_scope() as session:
        indexer = EmbeddingIndexer(session, provider)
        pending = indexer.pending_count(only_maritime=not args.include_non_maritime)
        if args.reset:
            print("Sletter alle eksisterende vektorer og bygger forfra ...")
        print(f"Model: {provider.info.model} ({provider.info.dimensions} dimensioner)")
        print(f"Mangler vektorer: {pending}")

        report = indexer.index_pending(
            limit=args.limit,
            only_maritime=not args.include_non_maritime,
            reset=args.reset,
        )
        remaining = indexer.pending_count(only_maritime=not args.include_non_maritime)

    print(f"Gennemgået      : {report.documents_checked}")
    print(f"Vektoriseret    : {report.documents_embedded}")
    print(f"Uden tekst      : {report.documents_skipped}")
    print(f"Fejlet          : {report.documents_failed}")
    print(f"Stykker skrevet : {report.chunks_written}")
    print(f"Stykker slettet : {report.chunks_deleted}")
    print(f"Mangler stadig  : {remaining}")
    for message in report.errors[:10]:
        print(f"  fejl: {message}")
    return 1 if report.documents_failed else 0


def cmd_embed_status(_: argparse.Namespace) -> int:
    """Viser dækning, model og hvorvidt databasen kan indeksere vektorer."""
    provider = _embedding_provider_or_exit()
    if provider is None:
        return 2

    with session_scope() as session:
        coverage = EmbeddingIndexer(session, provider).coverage()
        column_dimensions = vector_column_dimensions(session)
        log_stats = QueryLogService(session).stats()

    print(f"Udbyder          : {coverage['provider']}")
    print(f"Model            : {coverage['model']} ({coverage['dimensions']} dim.)")
    print(f"Semantisk        : {'ja' if coverage['semantic'] else 'NEJ (hash — kun test)'}")
    print(f"pgvector         : {'ja' if coverage['pgvector'] else 'nej (portabel brute force)'}")
    print(f"Maritime dok.    : {coverage['maritime_documents']}")
    print(f"  vektoriseret   : {coverage['embedded_documents']} ({coverage['coverage_pct']} %)")
    print(f"  mangler        : {coverage['pending_documents']}")
    print(f"Stykker i indeks : {coverage['chunks']}")
    if coverage["chunks_from_other_model"]:
        print(
            f"  fra anden model: {coverage['chunks_from_other_model']} "
            "— kør 'embed run --reset'"
        )
    if column_dimensions is not None and column_dimensions != coverage["dimensions"]:
        # Denne fejl er ubehagelig, fordi den først viser sig som en
        # databasefejl midt i en søgning. Derfor siges den højt her.
        print(
            f"ADVARSEL: pgvector-kolonnen har {column_dimensions} dimensioner, "
            f"men modellen giver {coverage['dimensions']}. "
            "Kør 'embed vector-column --recreate'."
        )

    print("Søgelog:")
    print(f"  forskellige søgninger : {log_stats['distinct_queries']}")
    print(f"  søgninger i alt       : {log_stats['total_searches']}")
    print(f"  uden resultat         : {log_stats['queries_without_results']}")
    print(f"  vektoriseret          : {log_stats['vectorized_queries']}")
    return 0


def cmd_embed_model_info(_: argparse.Namespace) -> int:
    """Indlæser modellen og bekræfter vektorlængden.

    Kommandoen der skal køres, før man skifter model: den fortæller om
    EMBEDDING_DIMENSIONS passer, i stedet for at man opdager det på en
    halvfærdig indeksering.
    """
    provider = _embedding_provider_or_exit()
    if provider is None:
        return 2

    print(f"Udbyder    : {provider.info.provider}")
    print(f"Model      : {provider.info.model}")
    print(f"Semantisk  : {'ja' if provider.info.semantic else 'nej'}")
    print(f"Beskrivelse: {provider.info.description}")

    try:
        vector = provider.embed_query("brandsikkerhed på passagerskibe")
    except Exception as exc:  # noqa: BLE001
        print(f"Prøvevektorisering mislykkedes: {exc}")
        return 2

    print(f"Konfigureret dimension: {provider.info.dimensions}")
    print(f"Faktisk dimension     : {len(vector)}")
    if len(vector) != provider.info.dimensions:
        print("FEJL: modellen og EMBEDDING_DIMENSIONS er ikke enige.")
        return 2
    print("Modellen svarer som forventet.")
    return 0


def cmd_embed_vector_column(args: argparse.Namespace) -> int:
    """Genskaber pgvector-kolonnen med den aktuelle models dimension.

    Nødvendig når embedding-modellen skiftes til en med anden
    vektorlængde: kolonnen blev oprettet med den gamle dimension i
    migration 0004, og en vektor af forkert længde afvises af databasen.
    """
    settings = get_settings()
    dimensions = settings.embedding_dimensions

    with session_scope() as session:
        if session.get_bind().dialect.name != "postgresql":
            print("Kun relevant på PostgreSQL. SQLite bruger den portable BLOB-kolonne.")
            return 0

        current = vector_column_dimensions(session)
        print(f"Nuværende kolonnedimension: {current if current is not None else 'ingen kolonne'}")
        print(f"Konfigureret dimension    : {dimensions}")

        if current == dimensions and not args.recreate:
            print("Ingen ændring nødvendig.")
            return 0

        if not args.recreate:
            print("Kør med --recreate for at genskabe kolonnen (alle vektorer slettes).")
            return 1

        for table in ("document_chunks", "search_queries"):
            session.execute(sql_text(f"DROP INDEX IF EXISTS ix_{table}_embedding_vec"))
            session.execute(sql_text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS embedding_vec"))
            session.execute(
                sql_text(f"ALTER TABLE {table} ADD COLUMN embedding_vec vector({dimensions})")
            )
            session.execute(
                sql_text(
                    f"CREATE INDEX ix_{table}_embedding_vec ON {table} "
                    "USING hnsw (embedding_vec vector_cosine_ops)"
                )
            )
        session.commit()
        reset_vector_support_cache()

    print("Kolonnen er genskabt. Byg indekset op igen: python -m app.cli embed run --reset")
    return 0


def cmd_search_log(args: argparse.Namespace) -> int:
    """Viser hvad der bliver søgt efter."""
    with session_scope() as session:
        service = QueryLogService(session)
        entries = (
            service.without_results(limit=args.limit)
            if args.without_results
            else service.popular(limit=args.limit)
        )
        rows = [(e.query_text, e.occurrences, e.last_result_count, e.best_result_count)
                for e in entries]

    if not rows:
        print("Søgeloggen er tom.")
        return 0

    heading = "Søgninger uden resultat" if args.without_results else "Hyppigste søgninger"
    print(heading)
    print(f"{'antal':>6}  {'træf':>6}  søgning")
    for text, occurrences, last, _best in rows:
        print(f"{occurrences:>6}  {last:>6}  {text}")
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

    discover_global = backfill_sub.add_parser(
        "discover-global",
        help="Find kandidater på tværs af myndigheder og skriv et CSV-manifest.",
        description="Søger myndighed for myndighed (fra config/discovery_global.yaml), "
        "forhåndsvurderer hvert fund med relevansmotoren, og mærker allerede kendte "
        "accessionsnumre. Lægger ALDRIG noget i køen og påvirker ikke den eksisterende "
        "Søfartsstyrelsen-opdagelse.",
    )
    discover_global.add_argument(
        "--source", choices=["fixture", "production"], default=None,
        help="Kilde. Standard er SOURCE_CLIENT fra konfigurationen.",
    )
    discover_global.add_argument(
        "--config", default=None,
        help="Sti til myndigheds-/deny-liste. Standard: config/discovery_global.yaml.",
    )
    discover_global.add_argument(
        "--authority", action="append", default=None,
        help="Begræns til denne myndighed. Kan gentages. Standard: alle i konfigurationen.",
    )
    discover_global.add_argument(
        "--status-current", default="Gældende", help="Kildens værdi for gældende dokumenter."
    )
    discover_global.add_argument(
        "--status-historical", default="Historisk", help="Kildens værdi for historiske."
    )
    discover_global.add_argument("--out", default=None, help="Sti til CSV-manifestet.")
    discover_global.add_argument(
        "--dry-run", action="store_true", help="Kør søgningerne uden at skrive CSV."
    )
    discover_global.set_defaults(func=cmd_backfill_discover_global)

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
        help="Nulstil poster der tidligere blev opgivet (FAILED).",
    )
    enqueue_manifest.add_argument(
        "--requeue-rejected", action="store_true",
        help="Nulstil poster der tidligere blev afvist af den automatiske "
        "motor (REJECTED). Adskilt fra --requeue-failed: REJECTED er en "
        "gyldig automatisk afgørelse, ikke en fejl.",
    )
    enqueue_manifest.add_argument(
        "--dry-run", action="store_true", help="Vis hvad der ville blive lagt i kø."
    )
    enqueue_manifest.set_defaults(func=cmd_backfill_enqueue_manifest)

    enqueue = backfill_sub.add_parser(
        "enqueue",
        help="Læg accessionsnumre i køen, evt. med en kurateret relevans-override.",
        description="Læg eksplicit angivne accessionsnumre i køen. Kan samtidig "
        "registrere en permanent, kurateret override af relevansafgørelsen for "
        "netop disse numre — se --curated-include/--curated-exclude.",
    )
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
        help="Mærkat for hvor listen kommer fra. Standard: manual. Bruges også "
        "som source_tag på en evt. curated override.",
    )
    enqueue.add_argument(
        "--priority", type=int, default=100, help="Lavere tal behandles først."
    )
    enqueue.add_argument(
        "--requeue-failed", action="store_true",
        help="Nulstil poster der tidligere blev opgivet (FAILED).",
    )
    enqueue.add_argument(
        "--requeue-rejected", action="store_true",
        help="Nulstil poster der tidligere blev afvist af den automatiske "
        "motor (REJECTED), så de behandles igen. Adskilt fra "
        "--requeue-failed: REJECTED er en gyldig automatisk afgørelse, ikke "
        "en fejl. Kun de eksplicit angivne accessionsnumre (--id/--file) "
        "påvirkes — aldrig hele køen.",
    )
    enqueue.add_argument(
        "--requeue-completed", action="store_true",
        help="Nulstil poster der allerede er færdigbehandlet (COMPLETED), så "
        "en netop registreret kurateret afgørelse slår igennem med det samme "
        "i stedet for ved en tilfældig senere genimport. KUN tilladt sammen "
        "med --curated-include/--curated-exclude, og kun for de eksplicit "
        "angivne accessionsnumre (--id/--file).",
    )
    curated_group = enqueue.add_mutually_exclusive_group()
    curated_group.add_argument(
        "--curated-include", action="store_true",
        help="Registrér en permanent, kurateret INCLUDE-afgørelse for netop "
        "disse accessionsnumre: effektiv is_maritime=True og køstatus "
        "COMPLETED ved næste import, uanset automatisk score. Den "
        "automatiske score og klassifikation ændres ikke. Kræver "
        "--curated-reason.",
    )
    curated_group.add_argument(
        "--curated-exclude", action="store_true",
        help="Registrér en permanent, kurateret EXCLUDE-afgørelse for netop "
        "disse accessionsnumre: effektiv is_maritime=False og køstatus "
        "REJECTED ved næste import, uanset automatisk score. Kræver "
        "--curated-reason.",
    )
    enqueue.add_argument(
        "--curated-reason", default=None, metavar="TEKST",
        help="Menneskelig begrundelse for overriden. Påkrævet sammen med "
        "--curated-include/--curated-exclude, og gemmes permanent sammen "
        "med afgørelsen.",
    )
    enqueue.add_argument(
        "--decided-by", default=None, metavar="NAVN",
        help="Hvem der traf beslutningen. Gemmes på afgørelsen og på hver "
        "historikpost.",
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

    curated_status = backfill_sub.add_parser(
        "curated-status", help="Vis registrerede kuraterede relevans-overrides."
    )
    curated_status.add_argument(
        "--decision", choices=CuratedDecision.values(), default=None,
        help="Filtrér på include/exclude.",
    )
    curated_status.add_argument("--tag", default=None, help="Filtrér på source_tag.")
    curated_status.add_argument(
        "--show", type=int, default=50, help="Antal poster der vises."
    )
    curated_status.set_defaults(func=cmd_backfill_curated_status)

    curated_history = backfill_sub.add_parser(
        "curated-history",
        help="Vis append-only historik for kuraterede afgørelser.",
        description="Viser hver registreret mutation af en kurateret "
        "afgørelse — også for accessionsnumre, hvis override siden er "
        "fjernet, og som derfor ikke fremgår af curated-status.",
    )
    curated_history.add_argument(
        "--accession", default=None, metavar="ACCN",
        help="Vis kun historik for dette accessionsnummer.",
    )
    curated_history.add_argument(
        "--show", type=int, default=50, help="Antal hændelser der vises."
    )
    curated_history.set_defaults(func=cmd_backfill_curated_history)

    # -- Semantisk indeks ---------------------------------------------------
    embed = sub.add_parser(
        "embed",
        help="Byg og inspicér det semantiske indeks (vektorer).",
        description=(
            "Vektorisering sker adskilt fra importen. Importen henter og "
            "gemmer lovteksten; denne kommando bygger indekset over den."
        ),
    )
    embed_sub = embed.add_subparsers(dest="embed_command", required=True)

    embed_run = embed_sub.add_parser(
        "run",
        help="Vektorisér de dokumenter der mangler.",
        description=(
            "Vektoriserer dokumenter, hvis vektorer mangler, stammer fra en "
            "ældre version eller er lavet med en anden model."
        ),
    )
    embed_run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Højst så mange dokumenter i denne kørsel. Standard: alle der mangler.",
    )
    embed_run.add_argument(
        "--include-non-maritime",
        action="store_true",
        help="Vektorisér også dokumenter der ikke er klassificeret som maritime.",
    )
    embed_run.add_argument(
        "--reset",
        action="store_true",
        help="Slet ALLE vektorer og byg forfra. Nødvendigt ved modelskifte.",
    )
    embed_run.set_defaults(func=cmd_embed_run)

    embed_status = embed_sub.add_parser(
        "status", help="Vis dækning, model og databasens vektorunderstøttelse."
    )
    embed_status.set_defaults(func=cmd_embed_status)

    embed_model = embed_sub.add_parser(
        "model-info",
        help="Indlæs modellen og bekræft vektorlængden.",
        description="Kør denne FØR et modelskifte — den fanger en forkert EMBEDDING_DIMENSIONS.",
    )
    embed_model.set_defaults(func=cmd_embed_model_info)

    embed_column = embed_sub.add_parser(
        "vector-column",
        help="Genskab pgvector-kolonnen med den aktuelle models dimension.",
    )
    embed_column.add_argument(
        "--recreate",
        action="store_true",
        help="Udfør ændringen. Uden dette flag vises kun hvad der ville ske.",
    )
    embed_column.set_defaults(func=cmd_embed_vector_column)

    # -- Søgelog ------------------------------------------------------------
    search_log = sub.add_parser(
        "search-log",
        help="Vis hvad der bliver søgt efter.",
        description=(
            "Loggen indeholder søgestrenge og antal — hverken bruger, "
            "IP-adresse eller session."
        ),
    )
    search_log.add_argument("--limit", type=int, default=20, help="Antal linjer.")
    search_log.add_argument(
        "--without-results",
        action="store_true",
        help="Vis kun søgninger der aldrig har givet et resultat.",
    )
    search_log.set_defaults(func=cmd_search_log)

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

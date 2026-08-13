"""Orkestrering af opdagelsen.

Ansvaret er snævert:

    kør søgningerne → fjern dubletter → kontrollér tallene → skriv CSV

Servicen henter ingen dokumenttekst, klassificerer ikke og rører ikke
databasen. Den lægger heller **ikke** noget i køen: manifestet skal
gennemgås af et menneske først. Det er hele pointen med at dele
opdagelse og efterindlæsning op.

TÆLLEKONTROLLEN
===============
Grundlaget er verificeret manuelt på Retsinformation med filteret
``administrerendeMyndighed = Søfartsstyrelsen``:

    Kun gældende      606
    Kun historisk   2.281
    Begge           2.887      (606 + 2.281 = 2.887)

Afviger en opdagelse fra det forventede, standser kommandoen **før**
CSV'en skrives. Et manifest, der stille mangler 400 dokumenter, er
farligere end ingen manifest: fejlen ville først vise sig som huller i
databasen måneder senere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger

from .base import DiscoveryClient, DiscoveryError, DiscoveryHit, DiscoveryQuery
from .manifest_csv import DEFAULT_DECISION, write_manifest

logger = get_logger(__name__)

__all__ = [
    "DiscoveryGroup",
    "DiscoveryReport",
    "DiscoveryService",
    "DiscoveryValidationError",
    "SOEFARTSSTYRELSEN_GROUPS",
    "VERIFIED_COUNTS",
]


class DiscoveryValidationError(DiscoveryError):
    """Tallene stemmer ikke. Manifestet er ikke skrevet."""


@dataclass(slots=True, frozen=True)
class DiscoveryGroup:
    """En delsøgning med et forventet antal.

    `expected = None` betyder "ingen forventning" — brugt ved fixtur- og
    prøvekørsler, hvor tallene naturligvis er andre.
    """

    label: str
    status: str | None
    expected: int | None = None


#: Manuelt verificeret på Retsinformation 13.08.2026.
VERIFIED_COUNTS = {"gældende": 606, "historisk": 2281, "total": 2887}

#: Standardopdelingen. Gældende og historiske hentes hver for sig, fordi
#: det er sådan tallene blev verificeret — og fordi en samlet søgning
#: ikke ville afsløre, at det ene filter holdt op med at virke.
SOEFARTSSTYRELSEN_GROUPS: tuple[DiscoveryGroup, ...] = (
    DiscoveryGroup(label="gældende", status="Gældende", expected=VERIFIED_COUNTS["gældende"]),
    DiscoveryGroup(label="historisk", status="Historisk", expected=VERIFIED_COUNTS["historisk"]),
)


@dataclass(slots=True)
class GroupOutcome:
    group: DiscoveryGroup
    found: int
    reported_total: int | None
    new: int
    duplicates: int
    pages: int
    truncated: bool


@dataclass(slots=True)
class DiscoveryReport:
    """Hvad opdagelsen fandt, og om det kan bruges."""

    authority: str
    outcomes: list[GroupOutcome] = field(default_factory=list)
    hits: list[DiscoveryHit] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    manifest_path: Path | None = None

    @property
    def total(self) -> int:
        return len(self.hits)

    @property
    def duplicates(self) -> int:
        return sum(outcome.duplicates for outcome in self.outcomes)

    @property
    def ok(self) -> bool:
        return not self.problems


class DiscoveryService:
    """Kører opdagelsen og skriver manifestet."""

    def __init__(self, client: DiscoveryClient) -> None:
        self._client = client

    @property
    def client_kind(self) -> str:
        return getattr(self._client, "kind", "ukendt")

    def discover(
        self,
        *,
        authority: str,
        groups: tuple[DiscoveryGroup, ...] | list[DiscoveryGroup],
        expected_total: int | None = None,
        output_path: Path | str | None = None,
        decision: str = DEFAULT_DECISION,
        allow_count_mismatch: bool = False,
    ) -> DiscoveryReport:
        """Kører alle delsøgninger og skriver manifestet.

        Raises:
            DiscoveryValidationError: hvis tallene ikke stemmer og
                `allow_count_mismatch` ikke er sat. CSV'en skrives ikke.
        """
        report = DiscoveryReport(authority=authority)
        seen: dict[str, DiscoveryHit] = {}

        for group in groups:
            query = DiscoveryQuery(authority=authority, status=group.status, label=group.label)
            result = self._client.search(query)

            new = 0
            duplicates = 0
            for hit in result.hits:
                if hit.accession_number in seen:
                    duplicates += 1
                    continue
                seen[hit.accession_number] = hit
                new += 1

            outcome = GroupOutcome(
                group=group,
                found=result.count,
                reported_total=result.reported_total,
                new=new,
                duplicates=duplicates,
                pages=result.pages_fetched,
                truncated=result.truncated,
            )
            report.outcomes.append(outcome)

            logger.info(
                "discovery.group.done",
                extra={
                    "authority": authority,
                    "group": group.label,
                    "found": outcome.found,
                    "new": new,
                    "duplicates": duplicates,
                    "reported_total": result.reported_total,
                },
            )
            report.problems.extend(_check_group(outcome))

        report.hits = sorted(seen.values(), key=lambda hit: hit.accession_number)

        if expected_total is not None and report.total != expected_total:
            report.problems.append(
                f"Samlet antal {report.total} afviger fra det forventede {expected_total}."
            )

        group_sum = sum(outcome.new + outcome.duplicates for outcome in report.outcomes)
        if report.duplicates:
            report.problems.append(
                f"{report.duplicates} accessionsnumre optrådte i flere delsøgninger "
                f"({group_sum} fundet, {report.total} unikke). Gældende og historiske "
                "bør ikke overlappe — kontrollér statusfilteret."
            )

        if report.problems and not allow_count_mismatch:
            raise DiscoveryValidationError(
                "Opdagelsen stemmer ikke med det forventede. Manifestet er IKKE skrevet.\n  - "
                + "\n  - ".join(report.problems)
            )

        if output_path is not None:
            comment = None
            if self.client_kind == "fixture":
                comment = (
                    "SYNTETISKE DATA — konstrueret til test. Ikke hentet fra Retsinformation."
                )
            elif report.problems:
                comment = "ADVARSEL: skrevet trods afvigende tal — " + "; ".join(report.problems)

            write_manifest(output_path, report.hits, decision=decision, header_comment=comment)
            report.manifest_path = Path(output_path)

        return report


def _check_group(outcome: GroupOutcome) -> list[str]:
    problems: list[str] = []
    label = outcome.group.label

    if outcome.truncated:
        problems.append(
            f"Delsøgningen '{label}' nåede sideloftet — resultatet er afkortet. "
            "Hæv RETSINFORMATION_SEARCH_MAX_PAGES."
        )

    if outcome.group.expected is not None and outcome.found != outcome.group.expected:
        problems.append(
            f"Delsøgningen '{label}' gav {outcome.found} resultater, "
            f"forventede {outcome.group.expected}."
        )

    if outcome.reported_total is not None and outcome.found != outcome.reported_total:
        problems.append(
            f"Delsøgningen '{label}' hentede {outcome.found} poster, men kilden "
            f"oplyser {outcome.reported_total}. Pagineringen ser ud til at mangle sider."
        )

    return problems

"""Global maritim opdagelse — søgning på tværs af myndigheder.

Udvider den eksisterende, myndighedsafgrænsede opdagelse
(:mod:`app.services.discovery.service`, brugt af `backfill discover` for
Søfartsstyrelsen) til at søge gennem en liste af andre myndigheder, hvor
maritim lovgivning historisk er udstedt eller administreret.

DESIGNBESLUTNING (se INSPEKTION-discover-global.md, afsnit 6)
===============================================================
Hver søgning er stadig afgrænset til ÉN myndighed ad gangen — nøjagtig
samme mønster som den eksisterende `discover`. Fagudtrykkene i
`config/maritime_keywords.yaml` bruges IKKE som en selvstændig søgeakse,
kun til FORHÅNDSVURDERING af hver fundet kandidat. Grunden:
:class:`~app.services.discovery.search_client.RetsinformationSearchClient`
overskriver hvert funds myndighedsfelt med forespørgslens myndighed — en
antagelse der forudsætter præcis én myndighed pr. søgning. At søge frit på
fagtermer uden myndighedsafgrænsning ville derfor give forkerte
myndighedsangivelser i CSV'en.

RENT LAG, INGEN DATABASEAFHÆNGIGHED
====================================
Denne pakke har bevidst ingen database- eller sessionsafhængighed, ligesom
resten af `app.services.discovery`. "Er dette allerede kendt?" afgøres af
kalderen (CLI'en), som slår accessionsnumre op i `backfill_manifest_items`
og `documents` og sender resultatet ind som et almindeligt `set[str]`.
Det holder discovery-laget testbart uden database og bevarer den
eksisterende arkitektoniske grænse.

INGEN SILENT CAPS
==================
En delsøgning kan støde på pagineringsproblemer eller uventede tal, ligesom
den myndighedsafgrænsede `discover`. Her er der ingen verificeret
facitliste at sammenligne med (kun Søfartsstyrelsen har det), så
`allow_count_mismatch` er altid slået til internt — men ethvert problem
optræder stadig i rapporten pr. myndighed. Intet fejer stille.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger
from app.core.text import fold
from app.services.relevance.base import RelevanceEngine
from app.services.retsinformation.base import NormalizedDocument

from .base import DiscoveryClient, DiscoveryHit
from .service import DiscoveryGroup, DiscoveryService

logger = get_logger(__name__)

__all__ = [
    "GlobalDiscoveryConfig",
    "GlobalDiscoveryReport",
    "PrescoredHit",
    "load_global_config",
    "discover_global",
]

#: Beslutningsværdier i CSV'ens 'decision'-kolonne.
DECISION_INCLUDE = "include"
DECISION_REVIEW = "review"
DECISION_EXCLUDE = "exclude"
#: Allerede i køen eller allerede gemt — ikke fundet forkert, blot ikke nyt.
DECISION_SKIP = "skip"


@dataclass(slots=True, frozen=True)
class GlobalDiscoveryConfig:
    """Indlæst `config/discovery_global.yaml`."""

    authorities: tuple[str, ...]
    #: Forkompilerede regex mod den FOLDEDE titel.
    deny_title_patterns: tuple[re.Pattern[str], ...]

    def title_is_denied(self, title: str | None) -> bool:
        if not title:
            return False
        folded = fold(title)
        return any(pattern.search(folded) for pattern in self.deny_title_patterns)


def load_global_config(path: Path | str) -> GlobalDiscoveryConfig:
    """Indlæser og validerer den globale opdagelseskonfiguration."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Konfigurationsfil mangler: {source}")

    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{source} skal indeholde et YAML-objekt")

    authorities = [
        str(a).strip() for a in (data.get("authorities") or []) if str(a).strip()
    ]
    if not authorities:
        raise ValueError(f"{source} indeholder ingen myndigheder under 'authorities'")

    patterns: list[re.Pattern[str]] = []
    for raw in data.get("deny_title_patterns") or []:
        text = str(raw).strip()
        if not text:
            continue
        try:
            patterns.append(re.compile(text, re.IGNORECASE))
        except re.error as exc:
            raise ValueError(f"Ugyldigt deny-mønster {text!r} i {source}: {exc}") from exc

    return GlobalDiscoveryConfig(
        authorities=tuple(dict.fromkeys(authorities)),  # bevar rækkefølge, fjern dubletter
        deny_title_patterns=tuple(patterns),
    )


@dataclass(slots=True)
class PrescoredHit:
    """Et fund beriget med forhåndsvurdering og kendthed.

    `decision` er den forslåede startværdi i CSV'ens 'decision'-kolonne —
    et menneske kan stadig overskrive den ved gennemgang. Ingen af disse
    felter fører nogensinde noget i køen af sig selv.
    """

    hit: DiscoveryHit
    prescore: int
    prescore_classification: str
    matched_terms: tuple[str, ...]
    already_known: bool
    decision: str


@dataclass(slots=True)
class AuthorityOutcome:
    authority: str
    found: int
    new: int
    duplicates: int
    problems: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GlobalDiscoveryReport:
    """Resultatet af en global opdagelse på tværs af myndigheder."""

    outcomes: list[AuthorityOutcome] = field(default_factory=list)
    hits: list[PrescoredHit] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.hits)

    @property
    def duplicates(self) -> int:
        return sum(o.duplicates for o in self.outcomes)

    @property
    def decision_counts(self) -> dict[str, int]:
        counts = {DECISION_INCLUDE: 0, DECISION_REVIEW: 0, DECISION_EXCLUDE: 0, DECISION_SKIP: 0}
        for prescored in self.hits:
            counts[prescored.decision] = counts.get(prescored.decision, 0) + 1
        return counts

    @property
    def problems(self) -> list[str]:
        return [p for outcome in self.outcomes for p in outcome.problems]


def _prescore(hit: DiscoveryHit, *, engine: RelevanceEngine) -> tuple[int, str, tuple[str, ...]]:
    """Vurderer et fund på titel + myndighed alene — ingen brødtekst.

    Samme motor som den afgørende klassifikation ved import, men uden
    indhold. Det gør forhåndsvurderingen billig (ingen dokumenthentning)
    og vejledende — den afgørende vurdering sker stadig først når
    dokumentet rent faktisk importeres med fuld tekst.
    """
    document = NormalizedDocument(
        source="discovery-global",
        source_id=hit.accession_number,
        title=hit.title or "",
        content="",
        authority=hit.authority,
        document_type=hit.document_type,
        status=hit.status,
    )
    result = engine.classify(document)
    return result.score, result.classification, tuple(result.matched_terms)


def _decide(
    *,
    denied: bool,
    already_known: bool,
    classification: str,
) -> str:
    # Allerede kendt vejer tungest — der er intet nyt at tage stilling til,
    # uanset hvor højt det scorer. Vises stadig i CSV'en, fjernes ikke.
    if already_known:
        return DECISION_SKIP
    if denied:
        return DECISION_EXCLUDE
    if classification == "maritime":
        return DECISION_INCLUDE
    if classification == "possible":
        return DECISION_REVIEW
    return DECISION_EXCLUDE


def discover_global(
    client: DiscoveryClient,
    *,
    config: GlobalDiscoveryConfig,
    relevance_engine: RelevanceEngine,
    status_current: str = "Gældende",
    status_historical: str = "Historisk",
    known_accessions: frozenset[str] = frozenset(),
) -> GlobalDiscoveryReport:
    """Søger igennem alle myndigheder i `config` og forhåndsvurderer fundene.

    Skriver ingen CSV — det gør kalderen (CLI'en), som også ejer
    filnavn/sti. Lægger intet i køen.

    Args:
        client: DELT klient på tværs af alle myndigheder. Vigtigt for
            :class:`RetsinformationSearchClient`, som cacher opløste
            ELI-stier internt for klientens levetid — en ny klient pr.
            myndighed ville miste den genbrug.
        known_accessions: Accessionsnumre der allerede findes i køen eller
            databasen. Slås op af kalderen; denne pakke rører ikke
            databasen selv.
    """
    report = GlobalDiscoveryReport()
    seen: dict[str, DiscoveryHit] = {}
    service = DiscoveryService(client)

    for authority in config.authorities:
        groups = [
            DiscoveryGroup(label="gældende", status=status_current, expected=None),
            DiscoveryGroup(label="historisk", status=status_historical, expected=None),
        ]

        try:
            authority_report = service.discover(
                authority=authority,
                groups=groups,
                expected_total=None,
                output_path=None,
                allow_count_mismatch=True,
            )
        except Exception as exc:  # netværks-/konfigurationsfejl for ÉN myndighed
            logger.warning(
                "discovery.global.authority_failed",
                extra={"authority": authority, "error": str(exc)},
            )
            report.outcomes.append(
                AuthorityOutcome(
                    authority=authority, found=0, new=0, duplicates=0,
                    problems=[f"Søgningen fejlede: {exc}"],
                )
            )
            continue

        new = duplicates = 0
        for hit in authority_report.hits:
            if hit.accession_number in seen:
                duplicates += 1
                continue
            seen[hit.accession_number] = hit
            new += 1

        report.outcomes.append(
            AuthorityOutcome(
                authority=authority,
                found=authority_report.total,
                new=new,
                duplicates=duplicates,
                problems=list(authority_report.problems),
            )
        )
        logger.info(
            "discovery.global.authority_done",
            extra={
                "authority": authority,
                "found": authority_report.total,
                "new": new,
                "duplicates": duplicates,
                "problems": len(authority_report.problems),
            },
        )

    for accession_number in sorted(seen):
        hit = seen[accession_number]
        already_known = accession_number in known_accessions
        denied = config.title_is_denied(hit.title)

        # Forhåndsvurder alligevel selv om allerede kendt/afvist — CSV'en
        # skal kunne bruges til at efterprøve BÅDE deny-listen og
        # dubletkontrollen, ikke kun de nye inklusioner.
        score, classification, matched_terms = _prescore(hit, engine=relevance_engine)
        decision = _decide(
            denied=denied, already_known=already_known, classification=classification
        )

        report.hits.append(
            PrescoredHit(
                hit=hit,
                prescore=score,
                prescore_classification=classification,
                matched_terms=matched_terms,
                already_known=already_known,
                decision=decision,
            )
        )

    return report

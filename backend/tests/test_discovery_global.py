"""Test af global maritim opdagelse (`backfill discover-global`).

Dækker det, der er særligt for denne kommando ud over den eksisterende
myndighedsafgrænsede `discover` (allerede dækket af test_discovery.py):
konfigurationsindlæsning, deny-listen, allerede-kendt-mærkning, og at
flere myndigheder aggregeres og deduplikeres korrekt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.discovery.base import DiscoveryHit, DiscoveryQuery, DiscoveryResult
from app.services.discovery.global_manifest import COLUMNS, write_global_manifest
from app.services.discovery.global_service import (
    GlobalDiscoveryConfig,
    PrescoredHit,
    discover_global,
    load_global_config,
)
from app.services.discovery.manifest_csv import read_manifest
from app.services.relevance.base import RelevanceResult
from app.services.relevance.keyword_engine import get_relevance_engine


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------


class TestLoadGlobalConfig:
    def test_loads_authorities_and_patterns(self, tmp_path: Path):
        path = tmp_path / "discovery_global.yaml"
        path.write_text(
            "authorities:\n  - Miljøministeriet\n  - Justitsministeriet\n"
            "deny_title_patterns:\n  - '^straffeloven'\n",
            encoding="utf-8",
        )
        config = load_global_config(path)
        assert config.authorities == ("Miljøministeriet", "Justitsministeriet")
        assert len(config.deny_title_patterns) == 1

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_global_config(tmp_path / "mangler.yaml")

    def test_no_authorities_raises(self, tmp_path: Path):
        path = tmp_path / "discovery_global.yaml"
        path.write_text("authorities: []\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_global_config(path)

    def test_invalid_regex_raises(self, tmp_path: Path):
        path = tmp_path / "discovery_global.yaml"
        path.write_text(
            "authorities:\n  - X\ndeny_title_patterns:\n  - '(unmatched'\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_global_config(path)

    def test_duplicate_authorities_are_collapsed_preserving_order(self, tmp_path: Path):
        path = tmp_path / "discovery_global.yaml"
        path.write_text(
            "authorities:\n  - A\n  - B\n  - A\n",
            encoding="utf-8",
        )
        config = load_global_config(path)
        assert config.authorities == ("A", "B")

    def test_real_config_file_loads(self):
        """Den faktiske config/discovery_global.yaml skal selv være gyldig."""
        from app.core.config import get_settings

        config = load_global_config(get_settings().discovery_global_config_path)
        assert len(config.authorities) >= 5
        assert len(config.deny_title_patterns) >= 1


class TestTitleIsDenied:
    def _config(self, *patterns: str) -> GlobalDiscoveryConfig:
        import re

        return GlobalDiscoveryConfig(
            authorities=("X",),
            deny_title_patterns=tuple(re.compile(p, re.IGNORECASE) for p in patterns),
        )

    def test_matches_folded_text(self):
        config = self._config("^faerdselsloven")
        assert config.title_is_denied("Færdselsloven (konsolideret)")

    def test_case_insensitive(self):
        config = self._config("straffeloven")
        assert config.title_is_denied("BEKENDTGØRELSE AF STRAFFELOVEN")

    def test_no_match_is_not_denied(self):
        config = self._config("^faerdselsloven")
        assert not config.title_is_denied("Bekendtgørelse om sikkerhed på passagerskibe")

    def test_empty_title_is_not_denied(self):
        config = self._config("^faerdselsloven")
        assert not config.title_is_denied(None)
        assert not config.title_is_denied("")


# ---------------------------------------------------------------------------
# discover_global — aggregering på tværs af myndigheder
# ---------------------------------------------------------------------------


class StubGlobalClient:
    """Opdagelsesklient med forudbestemte svar pr. (myndighed, status).

    I modsætning til `StubClient` i test_discovery.py, som kun nøgles på
    status, skal denne kunne give FORSKELLIGE svar for forskellige
    myndigheder — det er netop det, en global, myndighed-for-myndighed
    opdagelse har brug for at få afprøvet.
    """

    kind = "stub"

    def __init__(self, by_authority_status: dict[tuple[str, str | None], list[dict]]):
        self._data = by_authority_status
        self.failed_authorities: set[str] = set()

    def search(self, query: DiscoveryQuery) -> DiscoveryResult:
        if query.authority in self.failed_authorities:
            raise RuntimeError(f"kilden er nede for {query.authority}")
        records = self._data.get((query.authority, query.status), [])
        return DiscoveryResult(
            query=query,
            hits=[DiscoveryHit(source_query=query.describe(), **r) for r in records],
            reported_total=len(records),
            pages_fetched=1,
        )

    def close(self) -> None:
        return None


class StubRelevanceEngine:
    """Klassificerer alt ens — bruges til at isolere deny-listens effekt
    fra selve scoringen."""

    name = "stub"

    def __init__(self, score: int = 90, classification: str = "maritime"):
        self._score = score
        self._classification = classification

    def classify(self, document) -> RelevanceResult:
        return RelevanceResult(
            is_maritime=self._classification == "maritime",
            score=self._score,
            classification=self._classification,
            matched_terms=["stub"],
        )


def _config(*authorities: str, deny: tuple[str, ...] = ()) -> GlobalDiscoveryConfig:
    import re

    return GlobalDiscoveryConfig(
        authorities=authorities,
        deny_title_patterns=tuple(re.compile(p, re.IGNORECASE) for p in deny),
    )


class TestDiscoverGlobal:
    def test_aggregates_across_authorities(self):
        client = StubGlobalClient(
            {
                ("Miljøministeriet", "Gældende"): [
                    {"accession_number": "M1", "title": "Om ballastvand"}
                ],
                ("Justitsministeriet", "Gældende"): [
                    {"accession_number": "J1", "title": "Om søforklaring"}
                ],
            }
        )
        report = discover_global(
            client,
            config=_config("Miljøministeriet", "Justitsministeriet"),
            relevance_engine=StubRelevanceEngine(),
        )
        assert {p.hit.accession_number for p in report.hits} == {"M1", "J1"}
        assert report.total == 2
        assert len(report.outcomes) == 2

    def test_same_accession_across_authorities_is_deduplicated(self):
        # Kunstigt scenarie — men koden må ikke antage at accessionsnumre
        # er unikke pr. myndighed, hvis kilden af en eller anden grund
        # skulle levere det samme nummer to steder fra.
        client = StubGlobalClient(
            {
                ("A", "Gældende"): [{"accession_number": "X1", "title": "Første"}],
                ("B", "Gældende"): [{"accession_number": "X1", "title": "Første igen"}],
            }
        )
        report = discover_global(
            client, config=_config("A", "B"), relevance_engine=StubRelevanceEngine()
        )
        assert report.total == 1
        assert report.duplicates == 1

    def test_already_known_forces_skip_regardless_of_score(self):
        client = StubGlobalClient(
            {("A", "Gældende"): [{"accession_number": "X1", "title": "Om skibe"}]}
        )
        report = discover_global(
            client,
            config=_config("A"),
            relevance_engine=StubRelevanceEngine(score=95, classification="maritime"),
            known_accessions=frozenset({"X1"}),
        )
        [prescored] = report.hits
        assert prescored.already_known
        assert prescored.decision == "skip"

    def test_deny_list_overrides_a_high_score(self):
        """Selv et dokument scoret som maritimt skal afvises, hvis titlen
        matcher deny-listen — databasen skal ikke optage hele Straffeloven
        blot fordi én bestemmelse trigger relevansmotoren."""
        client = StubGlobalClient(
            {("Justitsministeriet", "Gældende"): [
                {"accession_number": "J1", "title": "Straffeloven (konsolideret)"}
            ]}
        )
        report = discover_global(
            client,
            config=_config("Justitsministeriet", deny=("^straffeloven",)),
            relevance_engine=StubRelevanceEngine(score=95, classification="maritime"),
        )
        [prescored] = report.hits
        assert prescored.decision == "exclude"
        assert prescored.prescore == 95, "scoren vises stadig — kun beslutningen overstyres"

    def test_low_score_is_review_not_exclude(self):
        client = StubGlobalClient(
            {("A", "Gældende"): [{"accession_number": "X1", "title": "Grænsetilfælde"}]}
        )
        report = discover_global(
            client,
            config=_config("A"),
            relevance_engine=StubRelevanceEngine(score=45, classification="possible"),
        )
        [prescored] = report.hits
        assert prescored.decision == "review"

    def test_one_authority_failing_does_not_stop_the_others(self):
        client = StubGlobalClient(
            {("B", "Gældende"): [{"accession_number": "X1", "title": "Om havne"}]}
        )
        client.failed_authorities = {"A"}
        report = discover_global(
            client, config=_config("A", "B"), relevance_engine=StubRelevanceEngine()
        )
        assert report.total == 1
        a_outcome = next(o for o in report.outcomes if o.authority == "A")
        assert a_outcome.problems
        assert "kilden er nede" in a_outcome.problems[0]

    def test_uses_the_real_relevance_engine_end_to_end(self):
        """Ingen stub — den rigtige motor skal kunne skelne et tydeligt
        maritimt fund fra et tydeligt ikke-maritimt et."""
        client = StubGlobalClient(
            {
                ("Miljøministeriet", "Gældende"): [
                    {
                        "accession_number": "M1",
                        "title": "Bekendtgørelse om forebyggelse af forurening fra skibe",
                        "authority": "Miljøministeriet",
                    },
                    {
                        "accession_number": "M2",
                        "title": "Bekendtgørelse om dagtilbud",
                        "authority": "Miljøministeriet",
                    },
                ]
            }
        )
        report = discover_global(
            client, config=_config("Miljøministeriet"), relevance_engine=get_relevance_engine()
        )
        by_id = {p.hit.accession_number: p for p in report.hits}
        assert by_id["M1"].decision == "include"
        assert by_id["M2"].decision == "exclude"


# ---------------------------------------------------------------------------
# CSV — skal kunne læses af den EKSISTERENDE enqueue-manifest
# ---------------------------------------------------------------------------


class TestGlobalManifestCsv:
    def _hit(self, **overrides) -> PrescoredHit:
        defaults = dict(
            hit=DiscoveryHit(
                accession_number="X1", title="Test", authority="A", source_query="q"
            ),
            prescore=70,
            prescore_classification="maritime",
            matched_terms=("skib", "søfart"),
            already_known=False,
            decision="include",
        )
        defaults.update(overrides)
        return PrescoredHit(**defaults)

    def test_written_file_has_bom_and_extra_columns(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        write_global_manifest(target, [self._hit()])
        raw = target.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        header = target.read_text(encoding="utf-8-sig").splitlines()[0]
        assert header.split(",") == list(COLUMNS)
        assert "prescore" in header and "already_known" in header

    def test_matched_terms_are_joined(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        write_global_manifest(target, [self._hit()])
        content = target.read_text(encoding="utf-8-sig")
        assert "skib; søfart" in content

    def test_already_known_rendered_as_ja(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        write_global_manifest(target, [self._hit(already_known=True, decision="skip")])
        content = target.read_text(encoding="utf-8-sig")
        assert ",ja," in content

    def test_readable_by_existing_enqueue_manifest_reader(self, tmp_path: Path):
        """Den eksisterende `read_manifest` kræver kun accession_number og
        decision — ekstra kolonner må ikke vælte den."""
        target = tmp_path / "m.csv"
        write_global_manifest(
            target,
            [
                self._hit(),
                self._hit(
                    hit=DiscoveryHit(accession_number="X2", title="Andet", source_query="q"),
                    decision="exclude",
                ),
            ],
        )
        rows = read_manifest(target)
        selected = [r.accession_number for r in rows if r.decision == "include"]
        assert selected == ["X1"]

    def test_rows_sorted_by_accession_number(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        write_global_manifest(
            target,
            [
                self._hit(hit=DiscoveryHit(accession_number="X2", source_query="q")),
                self._hit(hit=DiscoveryHit(accession_number="X1", source_query="q")),
            ],
        )
        lines = target.read_text(encoding="utf-8-sig").splitlines()
        assert lines[1].startswith("X1")
        assert lines[2].startswith("X2")

    def test_header_comment_marks_synthetic_data(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        write_global_manifest(target, [self._hit()], header_comment="SYNTETISK")
        assert target.read_text(encoding="utf-8-sig").startswith("# SYNTETISK")

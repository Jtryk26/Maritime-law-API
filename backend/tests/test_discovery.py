"""Tests for opdagelse af kandidat-accessionsnumre.

Tre lag afprøves hver for sig:

1. :mod:`app.services.discovery.extract` — det tolerante udtræk. Her
   ligger den største risiko, fordi svarformatet ikke er dokumenteret.
2. :mod:`app.services.discovery.search_client` — HTTP, paginering og de
   fejl der skal stoppe en kørsel frem for at give et halvt manifest.
3. :mod:`app.services.discovery.service` — tællekontrollen, som er den
   eneste beskyttelse mod stille datatab.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.services.discovery import (
    DiscoveryGroup,
    DiscoveryHit,
    DiscoveryQuery,
    DiscoveryService,
    DiscoveryValidationError,
    read_manifest,
    write_manifest,
)
from app.services.discovery.base import (
    DiscoveryConfigurationError,
    DiscoveryPaginationError,
    DiscoveryResponseError,
    DiscoveryResult,
)
from app.services.discovery.extract import (
    describe_payload,
    extract_accession_number,
    extract_hit_fields,
    find_record_list,
    find_reported_total,
)
from app.services.discovery.fixture import FixtureDiscoveryClient
from app.services.discovery.manifest_csv import COLUMNS
from app.services.discovery.search_client import RetsinformationSearchClient, render_request

REPO_ROOT = Path(__file__).resolve().parents[2]

TEMPLATE = {
    "administrerendeMyndighed": "{authority}",
    "retsinformationStatus": "{status}",
    "page": "{page}",
    "pageSize": "{page_size}",
}


def make_record(accession_number: str, **overrides):
    record = {
        "accessionsnummer": accession_number,
        "title": f"Bekendtgørelse {accession_number}",
        "administrerendeMyndighed": "Søfartsstyrelsen",
        "retsinformationStatus": "Gældende",
        "documentType": {"shortName": "BEK"},
        "publicationDate": "2024-03-05T00:00:00",
        "href": f"https://www.retsinformation.dk/eli/accn/{accession_number}",
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# Udtræk
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_accession_from_named_field(self):
        assert extract_accession_number({"accessionsnummer": "B20220122005"}) == "B20220122005"

    def test_accession_from_alternative_spelling(self):
        assert extract_accession_number({"AccessionNumber": "B20220122005"}) == "B20220122005"

    def test_accession_from_eli_url(self):
        record = {"href": "https://www.retsinformation.dk/eli/accn/B20220122005"}
        assert extract_accession_number(record) == "B20220122005"

    def test_accession_from_pattern_as_last_resort(self):
        assert extract_accession_number({"id": "AA000012605"}) == "AA000012605"

    def test_numeric_id_is_not_mistaken_for_accession(self):
        """Et almindeligt databasenøgle-id må ikke ende i køen."""
        assert extract_accession_number({"id": 41827, "title": "Noget"}) is None

    def test_missing_accession_returns_none(self):
        assert extract_accession_number({"title": "Bekendtgørelse om noget"}) is None

    def test_fields_are_extracted_from_nested_values(self):
        fields = extract_hit_fields(make_record("X20240000001"))
        assert fields["accession_number"] == "X20240000001"
        assert fields["document_type"] == "BEK"
        assert fields["authority"] == "Søfartsstyrelsen"
        assert fields["status"] == "Gældende"
        assert fields["published_date"] == date(2024, 3, 5)
        assert fields["eli_url"].endswith("/eli/accn/X20240000001")

    def test_missing_metadata_does_not_fail(self):
        fields = extract_hit_fields({"accessionsnummer": "X20240000001"})
        assert fields["accession_number"] == "X20240000001"
        assert fields["title"] is None
        assert fields["published_date"] is None

    def test_record_list_is_found_regardless_of_key_name(self):
        for key in ("documents", "items", "results", "hits"):
            payload = {key: [make_record("X20240000001"), make_record("X20240000002")]}
            assert len(find_record_list(payload)) == 2

    def test_unrelated_lists_are_ignored(self):
        payload = {
            "facets": [{"name": "Bekendtgørelse", "count": 12}, {"name": "Lov", "count": 3}],
            "documents": [make_record("X20240000001")],
        }
        records = find_record_list(payload)
        assert [extract_accession_number(r) for r in records] == ["X20240000001"]

    def test_bare_list_payload_is_supported(self):
        payload = [make_record("X20240000001")]
        assert len(find_record_list(payload)) == 1

    def test_reported_total_prefers_total_over_page_count(self):
        payload = {"totalCount": 606, "count": 100, "documents": []}
        assert find_reported_total(payload) == 606

    def test_reported_total_absent(self):
        assert find_reported_total({"documents": []}) is None

    def test_describe_payload_reports_structure(self):
        payload = {"totalCount": 2, "page": 1, "documents": [make_record("X20240000001")]}
        described = describe_payload(payload)
        assert described["records_found"] == 1
        assert described["reported_total"] == 2
        assert "page" in described["pagination_keys"]
        assert described["accession_numbers"] == ["X20240000001"]


# ---------------------------------------------------------------------------
# Anmodningsskabelon
# ---------------------------------------------------------------------------


class TestRenderRequest:
    def test_numeric_placeholders_become_numbers(self):
        rendered = render_request(
            TEMPLATE, authority="Søfartsstyrelsen", status="Gældende",
            page=2, page_size=100, offset=100,
        )
        assert rendered["page"] == 2
        assert rendered["pageSize"] == 100
        assert rendered["administrerendeMyndighed"] == "Søfartsstyrelsen"

    def test_empty_status_is_dropped(self):
        """Uden statusfilter må feltet ikke sendes som tom streng."""
        rendered = render_request(
            TEMPLATE, authority="Søfartsstyrelsen", status=None,
            page=1, page_size=100, offset=0,
        )
        assert "retsinformationStatus" not in rendered

    def test_offset_pagination(self):
        rendered = render_request(
            {"skip": "{offset}", "take": "{page_size}"},
            authority="X", status=None, page=3, page_size=50, offset=100,
        )
        assert rendered == {"skip": 100, "take": 50}

    def test_unknown_placeholder_is_rejected(self):
        with pytest.raises(DiscoveryConfigurationError):
            render_request(
                {"q": "{ukendt}"}, authority="X", status=None,
                page=1, page_size=10, offset=0,
            )


# ---------------------------------------------------------------------------
# Søgeklient
# ---------------------------------------------------------------------------


def build_client(handler, **kwargs) -> RetsinformationSearchClient:
    return RetsinformationSearchClient(
        url="https://example.invalid/search",
        method=kwargs.pop("method", "GET"),
        params_template=kwargs.pop("params_template", TEMPLATE),
        page_size=kwargs.pop("page_size", 2),
        min_request_interval=0,
        max_retries=kwargs.pop("max_retries", 1),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


class TestSearchClient:
    def test_missing_url_raises_configuration_error(self, monkeypatch):
        monkeypatch.setenv("RETSINFORMATION_SEARCH_URL", "")
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            with pytest.raises(DiscoveryConfigurationError) as excinfo:
                RetsinformationSearchClient()
            assert "probe-search" in str(excinfo.value)
        finally:
            get_settings.cache_clear()

    def test_pagination_collects_every_page(self):
        pages = {
            1: [make_record("X1000001"), make_record("X1000002")],
            2: [make_record("X1000003"), make_record("X1000004")],
            3: [make_record("X1000005")],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            return httpx.Response(200, json={"totalCount": 5, "documents": pages.get(page, [])})

        with build_client(handler) as client:
            result = client.search(DiscoveryQuery(authority="Søfartsstyrelsen"))

        assert result.count == 5
        assert result.pages_fetched == 3
        assert result.reported_total == 5
        assert not result.truncated

    def test_stops_when_reported_total_reached(self):
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            calls.append(page)
            documents = (
                [make_record("X1000001"), make_record("X1000002")] if page == 1 else []
            )
            return httpx.Response(200, json={"totalCount": 2, "documents": documents})

        with build_client(handler) as client:
            client.search(DiscoveryQuery(authority="Søfartsstyrelsen"))

        assert calls == [1], "kildens eget tal var nået efter side 1"

    def test_repeated_page_raises_instead_of_looping(self):
        """En ignoreret sideparameter må ikke give en uendelig løkke."""
        same = [make_record("X1000001"), make_record("X1000002")]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"documents": same})

        with build_client(handler, max_pages=50) as client:
            with pytest.raises(DiscoveryPaginationError):
                client.search(DiscoveryQuery(authority="Søfartsstyrelsen"))

    def test_page_limit_marks_result_truncated(self):
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            base = counter["n"] * 10
            return httpx.Response(
                200,
                json={"documents": [make_record(f"X100{base:04d}"), make_record(f"X100{base + 1:04d}")]},
            )

        with build_client(handler, max_pages=2) as client:
            result = client.search(DiscoveryQuery(authority="Søfartsstyrelsen"))

        assert result.truncated is True
        assert result.pages_fetched == 2

    def test_html_response_gives_actionable_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>...</html>",
                                  headers={"content-type": "text/html"})

        with build_client(handler) as client:
            with pytest.raises(DiscoveryResponseError) as excinfo:
                client.search(DiscoveryQuery(authority="Søfartsstyrelsen"))
        assert "HTML-siden" in str(excinfo.value)

    def test_client_error_is_not_retried(self):
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(400, json={"error": "bad request"})

        with build_client(handler, max_retries=3) as client:
            with pytest.raises(DiscoveryResponseError):
                client.search(DiscoveryQuery(authority="Søfartsstyrelsen"))

        assert len(calls) == 1

    def test_post_sends_json_body(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"documents": []})

        with build_client(handler, method="POST") as client:
            client.search(DiscoveryQuery(authority="Søfartsstyrelsen", status="Gældende"))

        assert seen["administrerendeMyndighed"] == "Søfartsstyrelsen"
        assert seen["retsinformationStatus"] == "Gældende"

    def test_records_without_accession_are_skipped_not_fatal(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            documents = (
                [make_record("X1000001"), {"title": "uden nummer"}] if page == 1 else []
            )
            return httpx.Response(200, json={"documents": documents})

        with build_client(handler) as client:
            result = client.search(DiscoveryQuery(authority="Søfartsstyrelsen"))

        assert [hit.accession_number for hit in result.hits] == ["X1000001"]


# ---------------------------------------------------------------------------
# Fixture-klient
# ---------------------------------------------------------------------------


class TestFixtureDiscoveryClient:
    def test_filters_on_authority_and_status(self):
        client = FixtureDiscoveryClient()
        current = client.search(DiscoveryQuery(authority="Søfartsstyrelsen", status="Gældende"))
        historical = client.search(
            DiscoveryQuery(authority="Søfartsstyrelsen", status="Historisk")
        )
        other = client.search(DiscoveryQuery(authority="Søfartsstyrelsen"))

        assert current.count == 6
        assert historical.count == 4
        assert other.count == 10, "uden statusfilter fås begge grupper"

    def test_other_authorities_are_excluded(self):
        client = FixtureDiscoveryClient()
        result = client.search(DiscoveryQuery(authority="Søfartsstyrelsen"))
        assert all(hit.authority == "Søfartsstyrelsen" for hit in result.hits)

    def test_fixture_file_is_marked_synthetic(self):
        path = REPO_ROOT / "data" / "fixtures" / "discovery_soefartsstyrelsen.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "SYNTETISKE" in payload["_notice"].upper()


# ---------------------------------------------------------------------------
# CSV-manifest
# ---------------------------------------------------------------------------


class TestManifestCsv:
    def _hits(self):
        return [
            DiscoveryHit(accession_number="X1000002", title="B", source_query="q"),
            DiscoveryHit(accession_number="X1000001", title="A", source_query="q"),
        ]

    def test_round_trip(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        assert write_manifest(target, self._hits()) == 2

        rows = read_manifest(target)
        assert [row.accession_number for row in rows] == ["X1000001", "X1000002"]
        assert all(row.decision == "include" for row in rows)

    def test_rows_are_sorted_for_readable_diffs(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        write_manifest(target, self._hits())
        lines = target.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0].split(",") == list(COLUMNS)
        assert lines[1].startswith("X1000001")

    def test_comment_lines_are_ignored_when_reading(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        write_manifest(target, self._hits(), header_comment="SYNTETISK")
        assert target.read_text(encoding="utf-8-sig").startswith("# SYNTETISK")
        assert len(read_manifest(target)) == 2

    def test_duplicate_rows_are_collapsed(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        target.write_text(
            "accession_number,decision\nX1000001,include\nX1000001,include\n",
            encoding="utf-8",
        )
        assert len(read_manifest(target)) == 1

    def test_missing_required_column_is_rejected(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        target.write_text("title,authority\nA,B\n", encoding="utf-8")
        with pytest.raises(ValueError):
            read_manifest(target)

    def test_danish_characters_survive(self, tmp_path: Path):
        target = tmp_path / "m.csv"
        write_manifest(
            target,
            [DiscoveryHit(accession_number="X1", title="Søfart og ålegræs", authority="Søfartsstyrelsen")],
        )
        assert "Søfart og ålegræs" in target.read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# Tællekontrol
# ---------------------------------------------------------------------------


class StubClient:
    """Opdagelsesklient med forudbestemte svar pr. statusfilter."""

    kind = "stub"

    def __init__(self, by_status: dict[str | None, list[str]], reported: dict | None = None):
        self._by_status = by_status
        self._reported = reported or {}

    def search(self, query: DiscoveryQuery) -> DiscoveryResult:
        numbers = self._by_status.get(query.status, [])
        return DiscoveryResult(
            query=query,
            hits=[
                DiscoveryHit(accession_number=n, source_query=query.describe()) for n in numbers
            ],
            reported_total=self._reported.get(query.status),
            pages_fetched=1,
        )

    def close(self) -> None:
        return None


GROUPS = (
    DiscoveryGroup(label="gældende", status="Gældende", expected=2),
    DiscoveryGroup(label="historisk", status="Historisk", expected=2),
)


class TestDiscoveryService:
    def test_happy_path_writes_manifest(self, tmp_path: Path):
        client = StubClient({"Gældende": ["X1", "X2"], "Historisk": ["X3", "X4"]})
        target = tmp_path / "m.csv"

        report = DiscoveryService(client).discover(
            authority="Søfartsstyrelsen", groups=GROUPS,
            expected_total=4, output_path=target,
        )

        assert report.ok
        assert report.total == 4
        assert target.is_file()
        assert [row.accession_number for row in read_manifest(target)] == ["X1", "X2", "X3", "X4"]

    def test_count_mismatch_stops_before_writing(self, tmp_path: Path):
        """Et ufuldstændigt manifest er farligere end ingen manifest."""
        client = StubClient({"Gældende": ["X1"], "Historisk": ["X3", "X4"]})
        target = tmp_path / "m.csv"

        with pytest.raises(DiscoveryValidationError) as excinfo:
            DiscoveryService(client).discover(
                authority="Søfartsstyrelsen", groups=GROUPS,
                expected_total=4, output_path=target,
            )

        assert "gældende" in str(excinfo.value)
        assert not target.exists(), "manifestet må ikke skrives ved afvigelse"

    def test_mismatch_can_be_overridden_and_is_marked_in_the_file(self, tmp_path: Path):
        client = StubClient({"Gældende": ["X1"], "Historisk": ["X3", "X4"]})
        target = tmp_path / "m.csv"

        report = DiscoveryService(client).discover(
            authority="Søfartsstyrelsen", groups=GROUPS, expected_total=4,
            output_path=target, allow_count_mismatch=True,
        )

        assert not report.ok
        assert target.read_text(encoding="utf-8-sig").startswith("# ADVARSEL")

    def test_reported_total_higher_than_fetched_is_a_problem(self, tmp_path: Path):
        """Manglende sider skal fanges, også når forventningen er slået fra."""
        client = StubClient(
            {"Gældende": ["X1", "X2"], "Historisk": ["X3", "X4"]},
            reported={"Gældende": 600},
        )
        groups = (
            DiscoveryGroup(label="gældende", status="Gældende"),
            DiscoveryGroup(label="historisk", status="Historisk"),
        )

        with pytest.raises(DiscoveryValidationError) as excinfo:
            DiscoveryService(client).discover(
                authority="Søfartsstyrelsen", groups=groups, output_path=tmp_path / "m.csv"
            )

        assert "600" in str(excinfo.value)

    def test_overlap_between_groups_is_deduplicated_and_reported(self, tmp_path: Path):
        client = StubClient({"Gældende": ["X1", "X2"], "Historisk": ["X2", "X3"]})

        report = DiscoveryService(client).discover(
            authority="Søfartsstyrelsen", groups=GROUPS, expected_total=4,
            output_path=tmp_path / "m.csv", allow_count_mismatch=True,
        )

        assert report.total == 3
        assert report.duplicates == 1
        assert any("flere delsøgninger" in problem for problem in report.problems)

    def test_no_expectation_means_no_validation(self, tmp_path: Path):
        client = StubClient({"Gældende": ["X1"], "Historisk": []})
        groups = (
            DiscoveryGroup(label="gældende", status="Gældende"),
            DiscoveryGroup(label="historisk", status="Historisk"),
        )

        report = DiscoveryService(client).discover(
            authority="Søfartsstyrelsen", groups=groups, output_path=tmp_path / "m.csv"
        )
        assert report.ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestDiscoveryCli:
    def test_discover_writes_manifest_and_enqueues_nothing(self, tmp_path: Path, capsys):
        from app.cli import main

        target = tmp_path / "m.csv"
        code = main(
            [
                "backfill", "discover", "--source", "fixture",
                "--out", str(target), "--expect-current", "6",
                "--expect-historical", "4",
            ]
        )
        output = capsys.readouterr().out

        assert code == 0
        assert target.is_file()
        assert len(read_manifest(target)) == 10
        assert "Intet er lagt i køen" in output
        assert "SYNTETISKE" in output

    def test_discover_fails_when_counts_do_not_match(self, tmp_path: Path):
        from app.cli import main

        target = tmp_path / "m.csv"
        code = main(
            [
                "backfill", "discover", "--source", "fixture",
                "--out", str(target), "--expect-current", "606",
                "--expect-historical", "2281",
            ]
        )

        assert code == 1
        assert not target.exists()

    def test_enqueue_manifest_respects_decision_column(self, tmp_path: Path, database_url, capsys):
        from app.cli import main

        target = tmp_path / "m.csv"
        target.write_text(
            "accession_number,decision\n"
            "X1000001,include\n"
            "X1000002,exclude\n"
            "X1000003,include\n",
            encoding="utf-8",
        )

        code = main(["backfill", "enqueue-manifest", "--file", str(target), "--tag", "test"])
        output = capsys.readouterr().out

        assert code == 0
        assert "Valgt        : 2" in output
        assert "Tilføjet     : 2" in output

        from app.db.session import session_scope
        from app.services.backfill import manifest as queue

        with session_scope() as session:
            pending = list(queue.pending_accessions(session, limit=10, source_tag="test"))
        assert pending == ["X1000001", "X1000003"]

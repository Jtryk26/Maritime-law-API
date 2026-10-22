"""``python -m app.cli vessel-rules`` — reglernes eneste skrivevej.

Kommandoerne køres gennem den rigtige ``main()`` mod testdatabasen, med
filer på disken eller standard input, og exitkoderne er en del af
kontrakten: 0 i orden, 1 ugyldigt/fejlet, 2 forkert brug, 3 konflikt.
"""

from __future__ import annotations

import io
import json

import pytest
import yaml
from sqlalchemy import select

from app.cli import main
from app.models import VesselApplicabilityRule
from app.services.vessel_applicability import evaluation_cache
from tests.vessel_rules_support import (
    CARGO_SURVEY,
    PAX_SURVEY,
    SAFETY_ORG,
    SEED,
    SEED_PATH,
    add_source_document,
    seed_source_documents,
)

FERRY = {
    "vessel_name": "Færge",
    "flag_state": "DK",
    "primary_type": "passenger",
    "operating_area": "international",
    "gross_tonnage": 3200,
    "max_passengers": 420,
    "actual_crew_count": 22,
    "keel_laid_date": "2010-01-01",
    "assessment_date": "2026-10-20",
}


@pytest.fixture(autouse=True)
def _clean_cache():
    evaluation_cache.invalidate_all()
    yield
    evaluation_cache.invalidate_all()


@pytest.fixture()
def sources(session):
    seed_source_documents(session)
    return session


def run(capsys, *argv: str) -> tuple[int, str]:
    code = main(["vessel-rules", *argv])
    return code, capsys.readouterr().out


def seed_dict() -> dict:
    return json.loads(SEED.model_dump_json(exclude_unset=True))


def write(tmp_path, payload: dict, name: str = "batch.json") -> str:
    path = tmp_path / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False)
        if name.endswith(".json")
        else yaml.safe_dump(payload),
        encoding="utf-8",
    )
    return str(path)


def rows(session) -> dict[str, VesselApplicabilityRule]:
    session.expire_all()
    return {r.rule_id: r for r in session.scalars(select(VesselApplicabilityRule))}


def hashes_from(output: str) -> dict[str, str]:
    """rule_id → content_hash fra en validate/ingest-udskrift."""
    found = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 3 and len(parts[1]) == 64:
            found[parts[2]] = parts[1]
    return found


def manifest(tmp_path, hashes: dict[str, str], name: str = "approvals.json") -> str:
    return write(tmp_path, {
        "schema_version": 1,
        "statement": "Kontrolleret mod Retsinformation i testen.",
        "approvals": [{"rule_id": r, "content_hash": h} for r, h in hashes.items()],
    }, name)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_skriver_intet_og_viser_hashes(capsys, sources):
    code, out = run(capsys, "validate", str(SEED_PATH))
    assert code == 0, out
    assert set(hashes_from(out)) == {PAX_SURVEY, CARGO_SURVEY, SAFETY_ORG}
    assert "Gyldig." in out
    assert rows(sources) == {}


def test_ikke_hoestet_kildedokument_afvises(capsys, session):
    code, out = run(capsys, "validate", str(SEED_PATH))
    assert code == 1
    assert "er ikke høstet" in out


def test_tekst_der_ikke_staar_ordret_i_kilden_afvises(capsys, sources, tmp_path):
    """Det, et hallucineret eller forældet citat ville give: 5 → 4."""
    payload = seed_dict()
    payload["drafts"][2]["source_text"] = payload["drafts"][2]["source_text"].replace(
        "mindst 5", "mindst 4"
    )
    code, out = run(capsys, "validate", write(tmp_path, payload))
    assert code == 1
    assert "findes ikke ordret" in out


def test_for_kort_citat_afvises(capsys, sources, tmp_path):
    payload = seed_dict()
    payload["drafts"][2]["source_text"] = "mindst 5 personer"
    code, out = run(capsys, "validate", write(tmp_path, payload))
    assert code == 1
    assert "for kort" in out


def test_ukendt_version_afvises(capsys, sources, tmp_path):
    payload = seed_dict()
    payload["drafts"][2]["source_document"]["version"] = 7
    code, out = run(capsys, "validate", write(tmp_path, payload))
    assert code == 1
    assert "ingen sådan version" in out


def test_kopieringsstoej_giver_samme_hash(capsys, sources, tmp_path):
    clean = seed_dict()
    noisy = seed_dict()
    text = noisy["drafts"][2]["source_text"]
    noisy["drafts"][2]["source_text"] = "﻿  " + text.replace(" ", " ", 3) + " \r\n\r\n"
    _, a = run(capsys, "validate", write(tmp_path, clean, "a.json"))
    _, b = run(capsys, "validate", write(tmp_path, noisy, "b.json"))
    assert hashes_from(a)[SAFETY_ORG] == hashes_from(b)[SAFETY_ORG]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["rule"].update(status="APPROVED"), "rule.status må ikke angives"),
        (lambda d: d.update(drafted_by="jacob"), "Extra inputs are not permitted"),
        (lambda d: d.pop("source_document"), "Field required"),
        (
            lambda d: d["rule"]["exclusion_conditions"].append(
                {"field": "primary_type", "operator": "eq", "target_value": "fishing_vessel"}
            ),
            "er ikke en gyldig værdi",
        ),
        (
            lambda d: d["rule"]["inclusion_conditions"].append(
                {"field": "propulsion_type", "operator": "eq", "target_value": "mechanical"}
            ),
            "antaget standardværdi",
        ),
    ],
)
def test_ugyldige_udkast_afvises(capsys, sources, tmp_path, mutate, message):
    payload = seed_dict()
    mutate(payload["drafts"][0])
    code, out = run(capsys, "validate", write(tmp_path, payload))
    assert code == 1
    assert message in out


def test_ulaeselig_fil_er_forkert_brug(capsys, tmp_path):
    assert run(capsys, "validate", str(tmp_path / "findes-ikke.json"))[0] == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{ikke json", encoding="utf-8")
    assert run(capsys, "validate", str(bad))[0] == 2


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def test_ingest_er_alt_eller_intet(capsys, sources, tmp_path):
    payload = seed_dict()
    payload["drafts"][1]["source_text"] = (
        "En tekst, som ikke findes i noget høstet dokument overhovedet."
    )
    code, out = run(capsys, "ingest", write(tmp_path, payload))
    assert code == 1
    assert "intet er skrevet" in out
    assert rows(sources) == {}


def test_ingest_fra_yaml_paa_standard_input(capsys, sources, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(SEED_PATH.read_text(encoding="utf-8")))
    code, out = run(capsys, "ingest", "-")
    assert code == 0, out

    stored = rows(sources)
    assert set(stored) == {PAX_SURVEY, CARGO_SURVEY, SAFETY_ORG}
    rule = stored[SAFETY_ORG]
    assert rule.status == "DRAFT"
    assert rule.drafted_by == "generator:seed-phase-1@5"
    assert rule.document_version is not None
    assert rule.events[0].note == "run_id=fixture"


def test_ingest_igen_er_uaendret_og_dry_run_skriver_intet(capsys, sources):
    assert run(capsys, "ingest", str(SEED_PATH), "--dry-run")[0] == 0
    assert rows(sources) == {}
    assert run(capsys, "ingest", str(SEED_PATH))[0] == 0
    code, out = run(capsys, "ingest", str(SEED_PATH))
    assert code == 0
    assert out.count("unchanged") == 3
    assert "Skrevet: 0 udkast" in out


def test_godkendt_regel_kan_ikke_overskrives(capsys, sources, tmp_path):
    _, out = run(capsys, "ingest", str(SEED_PATH))
    run(capsys, "approve", manifest(tmp_path, hashes_from(out)), "--reviewer", "jacob")

    payload = seed_dict()
    payload["drafts"][2]["rule"]["inclusion_conditions"][1]["target_value"] = 6
    code, out = run(capsys, "ingest", write(tmp_path, payload))
    assert code == 3
    assert "kan ikke overskrives" in out


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


def test_godkendelse_goer_reglerne_offentlige(capsys, sources, tmp_path, public_api_client):
    _, out = run(capsys, "ingest", str(SEED_PATH))
    code, out = run(
        capsys, "approve", manifest(tmp_path, hashes_from(out)), "--reviewer", "jacob"
    )
    assert code == 0, out

    rule = rows(sources)[SAFETY_ORG]
    assert rule.status == "APPROVED"
    assert rule.approved_by == "jacob"
    assert rule.approval_signature == "Kontrolleret mod Retsinformation i testen."
    assert rule.approved_at is not None

    report = public_api_client.post("/api/v1/applicability/evaluate", json=FERRY).json()
    assert report["rules_evaluated"] == 3
    approved = public_api_client.get("/api/v1/applicability/rules/approved").json()
    assert all(r["integrity_verified"] for r in approved)


def test_godkendelse_er_alt_eller_intet(capsys, sources, tmp_path):
    _, out = run(capsys, "ingest", str(SEED_PATH))
    hashes = hashes_from(out)
    hashes[CARGO_SURVEY] = "0" * 64
    code, out = run(capsys, "approve", manifest(tmp_path, hashes), "--reviewer", "jacob")
    assert code == 3
    assert "ændret, siden det blev gennemgået" in out
    assert {r.status for r in rows(sources).values()} == {"DRAFT"}


def test_ukendt_regel_i_manifest(capsys, sources, tmp_path):
    code, out = run(
        capsys, "approve", manifest(tmp_path, {"FINDES-IKKE": "0" * 64}), "--reviewer", "jacob"
    )
    assert code == 1
    assert "findes ikke" in out


@pytest.mark.parametrize("reviewer", ["generator:seed-phase-1@5", "Generator:noget", " "])
def test_maskine_kan_ikke_godkende(capsys, sources, tmp_path, reviewer):
    _, out = run(capsys, "ingest", str(SEED_PATH))
    code, out = run(capsys, "approve", manifest(tmp_path, hashes_from(out)), "--reviewer", reviewer)
    assert code == 3
    assert {r.status for r in rows(sources).values()} == {"DRAFT"}


def test_regel_bundet_til_sin_version_overlever_ny_version(capsys, sources, tmp_path):
    """Kilden ændres efter godkendelse: reglen gælder fortsat den version, den
    blev efterprøvet mod. Næste udkast mod 'aktuel' rammer den nye version."""
    _, out = run(capsys, "ingest", str(SEED_PATH))
    run(capsys, "approve", manifest(tmp_path, hashes_from(out)), "--reviewer", "jacob")
    add_source_document(
        sources, "TEST-BEK-2017-863", "Ændret: der skal være mindst 4 ansatte.", version=2
    )
    assert run(capsys, "verify")[0] == 0


# ---------------------------------------------------------------------------
# deprecate, list, show, verify
# ---------------------------------------------------------------------------


def test_udfasning_fjerner_reglen_fra_vurderingen(capsys, sources, tmp_path, public_api_client):
    _, out = run(capsys, "ingest", str(SEED_PATH))
    run(capsys, "approve", manifest(tmp_path, hashes_from(out)), "--reviewer", "jacob")

    code, _ = run(
        capsys, "deprecate", SAFETY_ORG, "--reviewer", "jacob", "--reason", "Ændret ved BEK 2027."
    )
    assert code == 0
    report = public_api_client.post("/api/v1/applicability/evaluate", json=FERRY).json()
    assert report["rules_evaluated"] == 2
    assert rows(sources)[SAFETY_ORG].events[-1].note == "Ændret ved BEK 2027."

    assert run(capsys, "deprecate", SAFETY_ORG, "--reviewer", "jacob", "--reason", "igen")[0] == 3
    assert run(capsys, "deprecate", "FINDES-IKKE", "--reviewer", "jacob", "--reason", "x")[0] == 1


def test_list_og_show(capsys, sources):
    run(capsys, "ingest", str(SEED_PATH))
    code, out = run(capsys, "list", "--status", "DRAFT", "--json")
    assert code == 0
    assert {r["rule_id"] for r in json.loads(out)} == {PAX_SURVEY, CARGO_SURVEY, SAFETY_ORG}

    code, out = run(capsys, "show", SAFETY_ORG)
    assert code == 0
    assert "Efterprøvet  : retsinformation:TEST-BEK-2017-863 version 1" in out
    assert "mindst 5 personer" in out

    code, out = run(capsys, "show", SAFETY_ORG, "--json")
    shown = json.loads(out)
    assert shown["source_document"]["source_id"] == "TEST-BEK-2017-863"
    assert shown["events"][0]["actor"] == "generator:seed-phase-1@5"

    assert run(capsys, "show", "FINDES-IKKE")[0] == 1


def test_verify_finder_aendret_kildetekst(capsys, sources, tmp_path):
    """Den dybe kontrol søger citatet i kilden igen; vurderingen gør ikke."""
    from app.models import DocumentVersion

    _, out = run(capsys, "ingest", str(SEED_PATH))
    run(capsys, "approve", manifest(tmp_path, hashes_from(out)), "--reviewer", "jacob")
    assert run(capsys, "verify")[0] == 0

    version = rows(sources)[SAFETY_ORG].document_version
    sources.get(DocumentVersion, version.id).content = "Teksten er blevet ændret i databasen."
    sources.commit()

    code, out = run(capsys, "verify", "--json")
    assert code == 1
    result = {r["rule_id"]: r for r in json.loads(out)}
    assert result[SAFETY_ORG]["verified"] is False
    assert "findes ikke ordret" in result[SAFETY_ORG]["problem"]
    assert result[PAX_SURVEY]["verified"] is True

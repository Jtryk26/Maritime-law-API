"""Kør det rigtige Bash-job med kontrollerede Docker- og HTTP-processer.

Ingen produktionsdatabase, kilde eller notifikationsmodtager kontaktes. Fejl,
processtop og rækkefølge afprøves ved grænsen til Docker/curl.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FAKE_TOOLS = r'''
import json
import os
import signal
import sys
import time
from pathlib import Path

root = Path(os.environ["JOB_TEST_ROOT"])
args = sys.argv[1:]
container = root / "container.json"

def event(**data):
    with (root / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(data) + "\n")

if Path(sys.argv[0]).name == "curl":
    config = sys.stdin.read()
    assert "test-private-ping" not in " ".join(args)
    kind = "start" if '/start"' in config else "fail" if '/fail"' in config else "success"
    event(ping=kind)
    sys.exit(22 if os.environ.get("FAIL_PING") == kind else 0)

if args[:1] == ["ps"]:
    label = args[-1].split("=", 2)[-1]
    if container.exists() and json.loads(container.read_text())["label"] == label:
        print("fake-job")
    sys.exit(0)

if args[:1] == ["rm"]:
    event(step="cleanup")
    if container.exists():
        pid = json.loads(container.read_text())["pid"]
        container.unlink()
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    sys.exit(0)

assert args[0] == "compose", args
assert "docker-compose.yml" in " ".join(args)
assert "docker-compose.tunnel.yml" in " ".join(args)
assert args[args.index("--profile") + 1] == "ops"
if "exec" in args:
    step = "verify_backup" if "pg_restore" in args else "backup"
    event(step=step)
    if step == os.environ.get("FAIL_STEP"):
        sys.exit(1)
    if step == "backup":
        assert args[-1] == 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'
        sys.stdout.buffer.write(b"PGDMP-test-dump")
    else:
        assert sys.stdin.buffer.read() == b"PGDMP-test-dump"
    sys.exit(0)

assert "run" in args and "--no-deps" in args and "-T" in args and "--rm" in args
assert "--service-ports" not in args
command = args[args.index("maintenance") + 1:]
assert command[:3] == ["python", "-m", "app.cli"]
step = "import" if command[3] == "import" else "embed_" + command[4]
if step in {"import", "embed_status"}:
    assert "--require-complete" in command
if step == "import":
    assert command[command.index("--source") + 1] == "production"
    assert "--since" in command
label = args[args.index("--label") + 1].split("=", 1)[1]
assert not container.exists(), "Jobcontainer allerede aktiv"
container.write_text(json.dumps({"pid": os.getpid(), "label": label}))
event(step=step, command=command)
if step == "import" and os.environ.get("HOLD_IMPORT"):
    (root / "import-ready").touch()
    while True:
        time.sleep(0.1)
container.unlink()
sys.exit(1 if step == os.environ.get("FAIL_STEP") else 0)
'''


@pytest.fixture()
def job(tmp_path):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    for filename in ("scheduled-import.sh", "import-settings.py"):
        shutil.copy2(ROOT / "scripts" / filename, project / "scripts" / filename)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "curl"):
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\n" + FAKE_TOOLS)
        tool.chmod(0o755)
    state = tmp_path / "state"
    backup = tmp_path / "backups"
    env_file = project / ".env"
    env_file.write_text(
        "IMPORT_HEALTHCHECK_URL='https://example.invalid/test-private-ping'\n"
        f"IMPORT_STATE_DIR='{state}'\nIMPORT_BACKUP_DIR='{backup}'\n"
        # Resten af .env kan indeholde komplekse Compose-værdier uden at eksekveres.
        "UNRELATED=$(exit 9)\n",
    )
    env_file.chmod(0o600)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", JOB_TEST_ROOT=str(tmp_path))
    env.pop("INVOCATION_ID", None)
    env.pop("SERVICE_RESULT", None)
    script = project / "scripts/scheduled-import.sh"
    return {"root": tmp_path, "project": project, "state": state, "backup": backup,
            "env": env, "script": script}


def run_job(job, *args, **env):
    return subprocess.run(
        ["bash", str(job["script"]), *args], env={**job["env"], **env},
        capture_output=True, text=True, timeout=15, check=False,
    )


def events(job):
    path = job["root"] / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def steps(job):
    return [entry["step"] for entry in events(job) if "step" in entry]


def pings(job):
    return [entry["ping"] for entry in events(job) if "ping" in entry]


def test_success_backs_up_before_writes_and_rotates_only_owned_files(job):
    job["backup"].mkdir()
    for name in ("pre-scheduled-import-old.dump", "manual-keep.dump"):
        path = job["backup"] / name
        path.write_text("old backup")
        old = time.time() - 16 * 86400
        os.utime(path, (old, old))
    result = run_job(job)
    assert result.returncode == 0, result.stderr
    assert steps(job) == ["backup", "verify_backup", "import", "embed_run", "embed_status"]
    assert pings(job) == ["start", "success"]
    assert not (job["backup"] / "pre-scheduled-import-old.dump").exists()
    assert (job["backup"] / "manual-keep.dump").exists()
    dumps = list(job["backup"].glob("pre-scheduled-import-*.dump"))
    assert len(dumps) == 1 and dumps[0].stat().st_mode & 0o777 == 0o600
    assert (job["state"] / "last-success-date").read_text().strip() == datetime.now(timezone.utc).date().isoformat()
    logs = "".join(path.read_text() for path in job["state"].glob("*.log"))
    assert "scheduled_import_completed" in logs
    assert "test-private-ping" not in logs


@pytest.mark.parametrize("failure", ["backup", "verify_backup", "import", "embed_run", "embed_status"])
def test_failure_aborts_pipeline_alerts_and_preserves_checkpoint(job, failure):
    job["state"].mkdir()
    checkpoint = job["state"] / "last-success-date"
    checkpoint.write_text("2026-08-01\n")
    result = run_job(job, FAIL_STEP=failure)
    assert result.returncode != 0
    sequence = ["backup", "verify_backup", "import", "embed_run", "embed_status"]
    assert steps(job) == sequence[:sequence.index(failure) + 1]
    assert pings(job) == ["start", "fail"]
    assert checkpoint.read_text() == "2026-08-01\n"
    assert not list(job["backup"].glob("*.partial"))


def test_window_overlaps_success_and_clamps_missed_feed(job):
    job["state"].mkdir()
    today = datetime.now(timezone.utc).date()
    checkpoint = job["state"] / "last-success-date"
    for age, expected in ((2, 3), (20, 10)):
        checkpoint.write_text((today - timedelta(days=age)).isoformat())
        result = run_job(job)
        assert result.returncode == 0
        command = [entry["command"] for entry in events(job) if entry.get("step") == "import"][-1]
        assert command[command.index("--since") + 1] == (today - timedelta(days=expected)).isoformat()


def start_held_import(job, **extra):
    process = subprocess.Popen(
        ["bash", str(job["script"])], env={**job["env"], "HOLD_IMPORT": "1", **extra},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while not (job["root"] / "import-ready").exists():
        if process.poll() is not None or time.monotonic() > deadline:
            os.killpg(process.pid, signal.SIGKILL)
            pytest.fail("Importtrinnet blev ikke startet")
        time.sleep(0.02)
    return process


def test_overlap_is_refused_and_sigterm_cleans_only_running_job(job):
    process = start_held_import(job)
    try:
        assert run_job(job).returncode == 75
        assert steps(job).count("import") == 1
        assert (job["root"] / "container.json").exists()
        # En anden systemd-invocation må ikke rydde denne container op.
        assert run_job(job, "--stop-post", INVOCATION_ID="other", SERVICE_RESULT="success").returncode == 0
        assert (job["root"] / "container.json").exists()
        process.terminate()
        assert process.wait(timeout=10) == 143
        assert steps(job)[-1] == "cleanup"
        assert pings(job)[-1] == "fail"
        assert not (job["root"] / "container.json").exists()
        assert not (job["state"] / "last-success-date").exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_systemd_stop_post_cleans_after_sigkill_and_alerts(job):
    invocation = "test-systemd-invocation"
    process = start_held_import(job, INVOCATION_ID=invocation)
    try:
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
        assert run_job(job, "--stop-post", INVOCATION_ID=invocation, SERVICE_RESULT="signal").returncode == 0
        assert not (job["root"] / "container.json").exists()
        assert pings(job) == ["start", "fail"]
        assert not (job["state"] / "last-success-date").exists()
        # Oprydning skal have frigivet låsen, så næste job kan lykkes.
        assert run_job(job).returncode == 0
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_unreadable_config_stops_before_backup_and_writes(job):
    (job["project"] / ".env").chmod(0o644)
    assert run_job(job).returncode != 0
    assert not events(job)


def test_bad_checkpoint_notifies_and_does_not_write(job):
    job["state"].mkdir()
    (job["state"] / "last-success-date").write_text("broken")
    assert run_job(job).returncode != 0
    assert not steps(job)
    assert pings(job) == ["fail"]


def test_failed_success_ping_is_not_reported_as_a_successful_job(job):
    assert run_job(job, FAIL_PING="success").returncode != 0
    assert pings(job) == ["start", "success", "fail"]

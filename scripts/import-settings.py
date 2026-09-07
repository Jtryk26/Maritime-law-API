"""Læs kun importjobbets indstillinger; .env må aldrig eksekveres som shellkode.

Kræver kun Pythons standardbibliotek på værten. Disse tre .env-felter bruger
enkeltlinjeværdier med valgfri citation og kommentar, uden variabelekspansion.
Docker Compose læser selv resten af .env med sin sædvanlige parser.
"""

from __future__ import annotations

import shlex
import stat
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit


def settings(project: Path) -> list[str]:
    env_file = project / ".env"
    if stat.S_IMODE(env_file.stat().st_mode) != 0o600:
        raise ValueError(".env skal have filtilstand 600.")
    defaults = {
        "IMPORT_HEALTHCHECK_URL": "",
        "IMPORT_STATE_DIR": str(Path.home() / ".local/state/maritim-import"),
        "IMPORT_BACKUP_DIR": str(Path.home() / "maritim-backups"),
    }
    values = defaults.copy()
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, separator, raw = line.partition("=")
        key = key.strip().removeprefix("export ")
        if separator and key in values:
            try:
                parts = shlex.split(raw, comments=True)
            except ValueError:
                raise ValueError(f"Ugyldig citation i {key}.") from None
            if len(parts) > 1:
                raise ValueError(f"Brug citation omkring {key}, hvis værdien har mellemrum.")
            values[key] = parts[0] if parts and parts[0] else defaults[key]
    for key, value in values.items():
        if any(ord(char) < 32 for char in value):
            raise ValueError(f"Kontroltegn er ikke tilladt i {key}.")
    url = urlsplit(values["IMPORT_HEALTHCHECK_URL"])
    if url.scheme != "https" or not url.hostname or url.query or url.fragment:
        raise ValueError("IMPORT_HEALTHCHECK_URL skal være Healthchecks' HTTPS-ping-URL uden query/fragment.")
    for key in ("IMPORT_STATE_DIR", "IMPORT_BACKUP_DIR"):
        if not Path(values[key]).is_absolute():
            raise ValueError(f"{key} skal være en absolut sti uden variabelekspansion.")

    return list(values.values())


def window(state_dir: Path) -> list[str]:
    """Genoptag fra sidste fuldførte import med én dags overlap inden for feeden."""
    today = datetime.now(timezone.utc).date()
    earliest = today - timedelta(days=10)
    checkpoint = state_dir / "last-success-date"
    since = earliest
    gap = "unknown"
    if checkpoint.exists():
        previous = date.fromisoformat(checkpoint.read_text().strip())
        if previous > today:
            raise ValueError("Importens seneste succesdato ligger i fremtiden.")
        since = max(earliest, previous - timedelta(days=1))
        gap = "yes" if previous < earliest else "no"
    return [since.isoformat(), today.isoformat(), gap]


if __name__ == "__main__":
    try:
        reader = window if sys.argv[1] == "--window" else settings
        print("\n".join(reader(Path(sys.argv[2]))))
    except (OSError, ValueError):
        # Parserfejl kan indeholde rå input; skriv aldrig URL eller filindhold.
        print("Importindstillinger kunne ikke læses. Kontrollér .env (600), URL, stier og checkpoint; se docs/scheduled-import.md.", file=sys.stderr)
        sys.exit(2)

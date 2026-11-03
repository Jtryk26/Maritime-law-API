"""Small maritime selection. Candidate identifiers are not verified legal status.

Only explicit original/consolidated versions are accepted; never resolve a
moving 'latest' alias or infer that an act is currently in force.
"""

import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_SELECTION = Path(__file__).resolve().parents[4] / "config" / "eurlex-maritime.yaml"


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    celex: str = Field(pattern=r"^3[0-9]{4}[RL][0-9]{4}$")
    version_celex: str = Field(pattern=r"^[03][0-9]{4}[RL][0-9]{4}(?:-[0-9]{8})?$")
    eli: str
    language: str = Field(default="en", pattern=r"^(en|da)$")
    note: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def exact_identity(self):
        if self.version_celex != self.celex:
            match = re.fullmatch(r"0([0-9]{4}[RL][0-9]{4})-([0-9]{8})", self.version_celex)
            if not match or "3" + match[1] != self.celex:
                raise ValueError("Version must belong to the selected CELEX act")
            date(int(match[2][:4]), int(match[2][4:6]), int(match[2][6:]))
        parsed = urlsplit(self.eli)
        kind = "reg" if self.celex[5] == "R" else "dir"
        expected = f"/eli/{kind}/{self.celex[1:5]}/{int(self.celex[6:])}/oj"
        if (parsed.scheme != "https" or parsed.netloc != "eur-lex.europa.eu"
                or parsed.path != expected or parsed.query or parsed.fragment):
            raise ValueError("ELI must identify the original selected act on EUR-Lex")
        return self

    @property
    def source_id(self) -> str:
        # One instrument, with exact editions/languages retained per version.
        return self.celex

    @property
    def source_version(self) -> dict:
        consolidated = self.version_celex.startswith("0")
        return {"celex": self.version_celex, "eli": self.eli,
                "kind": "consolidated" if consolidated else "original",
                "consolidation_date": self.version_celex.split("-")[1] if consolidated else None,
                "language": self.language}


def read_selection(path: Path = DEFAULT_SELECTION) -> list[Selection]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"records"}:
        raise ValueError("Selection must contain only records")
    records = [Selection.model_validate(item) for item in payload["records"]]
    if not 1 <= len(records) <= 10 or len({r.source_id for r in records}) != len(records):
        raise ValueError("Select 1–10 distinct act/language expressions per import")
    return records

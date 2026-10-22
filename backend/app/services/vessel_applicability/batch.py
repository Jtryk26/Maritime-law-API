"""Filformater for ``python -m app.cli vessel-rules``.

To formater, begge JSON eller YAML, fra fil eller standard input:

``DraftBatch``
    Udkast fra en generator (et script eller en sprogmodel, der har læst
    Retsinformation). Hver regel peger på den høstede dokumentversion, dens
    ``source_text`` skal findes ordret i.

``ApprovalManifest``
    Et menneskes godkendelse af præcis de ``(rule_id, content_hash)``-par,
    det har læst. Tidspunktet sættes af serveren, aldrig af filen.

Formaterne er strenge (``extra="forbid"``): et felt, systemet ikke kender, er
en fejl og ikke noget, der stille ignoreres.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ApplicabilityRule
from .service import SourceRef

__all__ = [
    "ApprovalEntry",
    "ApprovalManifest",
    "BatchFileError",
    "DraftBatch",
    "DraftEntry",
    "GeneratorInfo",
    "SourceDocumentRef",
    "load_payload",
]


class BatchFileError(ValueError):
    """Filen kan ikke læses eller tolkes."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneratorInfo(_Strict):
    """Hvem der har dannet udkastene. Bliver til ``drafted_by``."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$")
    run_id: str | None = Field(default=None, max_length=128)

    @property
    def actor(self) -> str:
        return f"generator:{self.name}@{self.version}"


class SourceDocumentRef(_Strict):
    source: str = Field(default="retsinformation", min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=128)
    #: Udeladt = dokumentets aktuelle version på udkasttidspunktet. Den
    #: version, der faktisk blev brugt, bindes til reglen uanset hvad.
    version: int | None = Field(default=None, ge=1)

    def to_ref(self) -> SourceRef:
        return SourceRef(source=self.source, source_id=self.source_id, version_number=self.version)


class DraftEntry(_Strict):
    rule: ApplicabilityRule
    source_text: str = Field(min_length=1, max_length=200_000)
    source_document: SourceDocumentRef

    @model_validator(mode="after")
    def _no_status(self) -> DraftEntry:
        if "status" in self.rule.model_fields_set:
            raise ValueError(
                "rule.status må ikke angives: alt indlæses som DRAFT og godkendes "
                "kun med 'vessel-rules approve'"
            )
        return self


class DraftBatch(_Strict):
    schema_version: Literal[1]
    generator: GeneratorInfo
    drafts: list[DraftEntry] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _unique_rule_ids(self) -> DraftBatch:
        _require_unique([d.rule.rule_id for d in self.drafts])
        return self


class ApprovalEntry(_Strict):
    rule_id: str = Field(min_length=1, max_length=128)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApprovalManifest(_Strict):
    schema_version: Literal[1]
    #: Gemmes som godkendelsens ``signature`` på hver regel: hvad anmelderen
    #: har kontrolleret, med egne ord.
    statement: str = Field(min_length=10, max_length=4096)
    approvals: list[ApprovalEntry] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _unique_rule_ids(self) -> ApprovalManifest:
        _require_unique([a.rule_id for a in self.approvals])
        return self


def _require_unique(rule_ids: list[str]) -> None:
    seen, duplicates = set(), set()
    for rule_id in rule_ids:
        (duplicates if rule_id in seen else seen).add(rule_id)
    if duplicates:
        raise ValueError(f"rule_id forekommer flere gange: {', '.join(sorted(duplicates))}")


def load_payload(location: str) -> Any:
    """Læs JSON eller YAML fra en sti eller ``-`` (standard input).

    ``.json`` læses som JSON; alt andet med YAML's sikre indlæser, som også
    forstår JSON.
    """
    try:
        if location == "-":
            raw, is_json = sys.stdin.read(), False
        else:
            path = Path(location)
            raw, is_json = path.read_text(encoding="utf-8"), path.suffix.lower() == ".json"
    except OSError as exc:
        raise BatchFileError(f"Kan ikke læse {location}: {exc}") from exc
    try:
        return json.loads(raw) if is_json else yaml.safe_load(raw)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise BatchFileError(f"{location} er hverken gyldig JSON eller YAML: {exc}") from exc

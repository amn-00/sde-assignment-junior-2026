"""Loads the per-workflow lead schemas and the disposition catalogue."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import normalize_header

DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "assignment-a-data" / "schemas"


@dataclass
class Field:
    name: str
    type: str                # string | phone | date | decimal | enum
    required: bool = False
    pii: bool = False
    reference: bool = False
    default: str | None = None
    values: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


@dataclass
class WorkflowSchema:
    workflow_id: str
    fields: list[Field]

    @property
    def reference_field(self) -> str:
        return next(f.name for f in self.fields if f.reference)

    @property
    def phone_field(self) -> str:
        return next(f.name for f in self.fields if f.type == "phone")

    def header_lookup(self) -> dict[str, str]:
        """normalised header/alias -> field name."""
        lookup = {}
        for f in self.fields:
            for h in [f.name, *f.aliases]:
                lookup.setdefault(normalize_header(h), f.name)
        return lookup


@dataclass
class Disposition:
    is_terminal: bool
    is_success: bool


def load_schemas(schema_dir: Path = DEFAULT_SCHEMA_DIR) -> dict[str, WorkflowSchema]:
    schemas = {}
    for path in sorted(schema_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        fields = [Field(**{k: v for k, v in f.items() if k in Field.__dataclass_fields__})
                  for f in raw["fields"]]
        schemas[raw["workflow_id"]] = WorkflowSchema(raw["workflow_id"], fields)
    return schemas


def load_dispositions(schema_dir: Path = DEFAULT_SCHEMA_DIR) -> dict[str, dict[str, Disposition]]:
    """{workflow_id: {code: Disposition}}"""
    out: dict[str, dict[str, Disposition]] = {}
    with open(schema_dir / "dispositions.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.setdefault(row["workflow_id"], {})[row["code"]] = Disposition(
                is_terminal=row["is_terminal"].strip().lower() == "true",
                is_success=row["is_success"].strip().lower() == "true",
            )
    return out

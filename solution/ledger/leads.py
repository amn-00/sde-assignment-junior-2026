"""Lead file ingestion: header mapping, validation, rejection, merging."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass

from .catalog import Field, WorkflowSchema
from .crypto import Pii
from .normalize import normalize_header, normalize_phone, parse_date, parse_decimal, parse_enum


@dataclass
class ParsedRow:
    values: dict[str, str | None]   # field name -> normalised value (None = missing/invalid)
    reason: str | None              # rejection reason, None if the row is valid
    warnings: int                   # invalid optional fields (only meaningful if not rejected)


def map_headers(header: list[str], schema: WorkflowSchema) -> dict[int, str]:
    """column index -> field name. Unknown columns are ignored; first matching column wins."""
    lookup = schema.header_lookup()
    mapping, taken = {}, set()
    for i, h in enumerate(header):
        name = lookup.get(normalize_header(h))
        if name and name not in taken:
            mapping[i] = name
            taken.add(name)
    return mapping


def _parse_value(f: Field, raw: str) -> str | None:
    if f.type == "string":
        return raw
    if f.type == "phone":
        return normalize_phone(raw)
    if f.type == "date":
        return parse_date(raw)
    if f.type == "decimal":
        return parse_decimal(raw)
    if f.type == "enum":
        return parse_enum(raw, f.values)
    raise ValueError(f"unknown field type {f.type!r}")


def parse_row(schema: WorkflowSchema, raw: dict[str, str]) -> ParsedRow:
    """raw: field name -> raw cell text (fields with no column are absent).

    Fields are walked in schema order; the first failure becomes the single rejection reason.
    """
    values: dict[str, str | None] = {}
    reason, warnings = None, 0
    for f in schema.fields:
        cell = (raw.get(f.name) or "").strip()
        if not cell:
            values[f.name] = None
            if f.required and reason is None:
                reason = f"missing_required:{f.name}"
            continue
        parsed = _parse_value(f, cell)
        values[f.name] = parsed
        if parsed is None:
            if f.type == "phone":
                reason = reason or "invalid_phone"
            elif f.required:
                # not covered by the spec; a required value we can't parse is unusable
                reason = reason or f"invalid:{f.name}"
            else:
                warnings += 1
    return ParsedRow(values, reason, warnings if reason is None else 0)


def ingest_lead_file(conn: sqlite3.Connection, pii: Pii, schema: WorkflowSchema,
                     file_id: int, batch_id: str, text: str) -> None:
    """Runs inside the caller's transaction."""
    reader = csv.reader(io.StringIO(text))
    columns = map_headers(next(reader, []), schema)
    pii_fields = {f.name for f in schema.fields if f.pii}
    defaults = {f.name: f.default for f in schema.fields if f.default is not None}
    phone_field, ref_field = schema.phone_field, schema.reference_field

    row_number = 0
    for cells in reader:
        if all(not c.strip() for c in cells):
            continue  # blank row: skipped and not counted
        row_number += 1
        raw = {name: cells[i] for i, name in columns.items() if i < len(cells)}
        parsed = parse_row(schema, raw)

        if parsed.reason:
            _record(conn, file_id, row_number, schema.workflow_id, batch_id, "rejected",
                    parsed.reason, 0)
            continue

        phone = parsed.values[phone_field]
        phone_hmac = pii.phone_hmac(phone)
        existing = conn.execute(
            "SELECT lead_id, attributes, pii FROM leads WHERE workflow_id = ? AND phone_hmac = ?",
            (schema.workflow_id, phone_hmac)).fetchone()

        new_attrs = {k: v for k, v in parsed.values.items() if k not in pii_fields}
        new_pii = {k: pii.encrypt(v) for k, v in parsed.values.items()
                   if k in pii_fields and v is not None}

        if existing is None:
            # Defaults are applied to fields still null. Applying them on create is equivalent
            # to "after merging": a later row only overwrites with non-null values.
            attrs = {k: (v if v is not None else defaults.get(k)) for k, v in new_attrs.items()}
            conn.execute(
                "INSERT INTO leads (workflow_id, phone_hmac, phone_last4, batch_id, external_ref,"
                " attributes, pii) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (schema.workflow_id, phone_hmac, phone[-4:], batch_id, attrs[ref_field],
                 json.dumps(attrs, sort_keys=True), json.dumps(new_pii, sort_keys=True)))
            outcome = "created"
        else:
            lead_id, attrs_json, pii_json = existing
            attrs = json.loads(attrs_json)
            attrs.update({k: v for k, v in new_attrs.items() if v is not None})  # last write wins
            merged_pii = {**json.loads(pii_json), **new_pii}
            conn.execute(
                "UPDATE leads SET external_ref = ?, attributes = ?, pii = ? WHERE lead_id = ?",
                (attrs[ref_field], json.dumps(attrs, sort_keys=True),
                 json.dumps(merged_pii, sort_keys=True), lead_id))
            outcome = "merged"

        _record(conn, file_id, row_number, schema.workflow_id, batch_id, outcome, None,
                parsed.warnings)


def _record(conn, file_id, row_number, workflow_id, batch_id, outcome, reason, warnings):
    conn.execute(
        "INSERT INTO lead_rows (file_id, row_number, workflow_id, batch_id, outcome, reason,"
        " warnings) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (file_id, row_number, workflow_id, batch_id, outcome, reason, warnings))

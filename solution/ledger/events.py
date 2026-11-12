"""Event file ingestion into the append-only ledger.

Only malformed lines and duplicate event_ids are decided here. Linking (orphans) and unknown
dispositions are decided at report time from the full ledger, because a lead may arrive later.
"""
from __future__ import annotations

import json
import sqlite3

from .crypto import Pii
from .normalize import normalize_phone, parse_timestamp, to_epoch_us


def ingest_event_file(conn: sqlite3.Connection, pii: Pii, file_id: int, text: str) -> None:
    """Runs inside the caller's transaction."""
    # split on '\n' only: splitlines() would also break on unicode separators inside strings
    for line_number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue  # empty lines aren't counted at all

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = None
        event_id = event.get("event_id") if isinstance(event, dict) else None
        if not isinstance(event_id, str) or not event_id.strip():
            _issue(conn, file_id, line_number, "malformed", None)
            continue

        phone = normalize_phone(event.get("phone"))
        ts = parse_timestamp(event.get("occurred_at"))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

        cur = conn.execute(
            "INSERT OR IGNORE INTO events (event_id, file_id, line_number, event_type, workflow_id,"
            " call_id, occurred_at, phone_hmac, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, file_id, line_number, event.get("event_type"), event.get("workflow_id"),
             event.get("call_id"), to_epoch_us(ts) if ts else None,
             pii.phone_hmac(phone) if phone else None, json.dumps(payload, sort_keys=True)))
        if cur.rowcount == 0:
            _issue(conn, file_id, line_number, "duplicate", event_id)


def _issue(conn, file_id, line_number, issue, event_id):
    conn.execute(
        "INSERT INTO event_line_issues (file_id, line_number, issue, event_id) VALUES (?, ?, ?, ?)",
        (file_id, line_number, issue, event_id))

"""Drop discovery and the idempotent, per-file transactional ingest."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from .catalog import WorkflowSchema
from .crypto import Pii
from .events import ingest_event_file
from .leads import ingest_lead_file


def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def expand_drops(paths: list[str]) -> list[Path]:
    """Each path is either a drop dir (has leads/ or events/) or a folder of drop dirs."""
    drops = []
    for p in map(Path, paths):
        if not p.is_dir():
            raise SystemExit(f"not a directory: {p}")
        if (p / "leads").is_dir() or (p / "events").is_dir():
            drops.append(p)
        else:
            children = [c for c in p.iterdir()
                        if c.is_dir() and ((c / "leads").is_dir() or (c / "events").is_dir())]
            if not children:
                raise SystemExit(f"no drop found in {p}")
            drops.extend(sorted(children, key=_natural_key))
    return drops


def ingest_drop(conn: sqlite3.Connection, pii: Pii, schemas: dict[str, WorkflowSchema],
                drop: Path) -> dict[str, int]:
    """Leads first (workflow by name, files by filename), then events (by filename)."""
    files = []  # (schema or None for events, path), in processing order
    leads_dir = drop / "leads"
    if leads_dir.is_dir():
        for wf_dir in sorted(d for d in leads_dir.iterdir() if d.is_dir()):
            schema = schemas.get(wf_dir.name)
            if schema is None:
                print(f"[ledger] WARNING: no schema for workflow '{wf_dir.name}', skipping")
                continue
            files += [(schema, p) for p in sorted(wf_dir.glob("*.csv"), key=_natural_key)]
    events_dir = drop / "events"
    if events_dir.is_dir():
        files += [(None, p) for p in sorted(events_dir.glob("*.jsonl"), key=_natural_key)]

    stats = {"files_ingested": 0, "files_skipped": 0}
    for schema, path in files:
        if _ingest_file(conn, pii, schema, path):
            stats["files_ingested"] += 1
        else:
            stats["files_skipped"] += 1
    return stats


def _ingest_file(conn: sqlite3.Connection, pii: Pii, schema: WorkflowSchema | None,
                 path: Path) -> bool:
    """One transaction per file: the file is either fully ingested and recorded, or not at all.
    A file already recorded (same name + same content hash) is skipped -> re-runs are no-ops.
    `schema` is set for a lead file and None for an event file. Returns False if skipped."""
    kind = "leads" if schema else "events"
    workflow_id = schema.workflow_id if schema else None
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    conn.execute("BEGIN IMMEDIATE")  # check + write under one lock
    try:
        seen = conn.execute(
            "SELECT 1 FROM ingested_files WHERE kind = ? AND workflow_id IS ? AND file_name = ?"
            " AND sha256 = ?", (kind, workflow_id, path.name, sha)).fetchone()
        if seen:
            conn.execute("ROLLBACK")
            return False
        file_id = conn.execute(
            "INSERT INTO ingested_files (kind, workflow_id, file_name, sha256) VALUES (?, ?, ?, ?)",
            (kind, workflow_id, path.name, sha)).lastrowid
        text = data.decode("utf-8-sig")  # utf-8-sig also strips a BOM
        if schema:
            ingest_lead_file(conn, pii, schema, file_id, path.stem, text)
        else:
            ingest_event_file(conn, pii, file_id, text)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return True

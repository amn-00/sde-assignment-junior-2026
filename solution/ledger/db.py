"""SQLite schema.

ingested_files     one row per input file, keyed by content hash -> makes re-ingest a no-op
lead_rows          outcome of every non-blank lead row (created / merged / rejected). No PII.
leads              one row per (workflow_id, phone_hmac). PII encrypted, phone only as HMAC.
events             the interaction ledger. Append-only (triggers block UPDATE/DELETE).
                   Phone is stored only as an HMAC. Linking to leads happens at report time,
                   so an event whose lead arrives in a later drop links automatically.
event_line_issues  malformed / duplicate event lines (kept so counts are derivable, no PII).
"""
from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS ingested_files (
    file_id     INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('leads', 'events')),
    workflow_id TEXT,                         -- leads only
    file_name   TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (kind, workflow_id, file_name, sha256)
);

CREATE TABLE IF NOT EXISTS lead_rows (
    file_id     INTEGER NOT NULL REFERENCES ingested_files(file_id),
    row_number  INTEGER NOT NULL,
    workflow_id TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    outcome     TEXT NOT NULL CHECK (outcome IN ('created', 'merged', 'rejected')),
    reason      TEXT,                         -- rejected rows only
    warnings    INTEGER NOT NULL DEFAULT 0,   -- invalid optional fields (non-rejected rows)
    PRIMARY KEY (file_id, row_number)
);

CREATE TABLE IF NOT EXISTS leads (
    lead_id      INTEGER PRIMARY KEY,
    workflow_id  TEXT NOT NULL,
    phone_hmac   TEXT NOT NULL,
    phone_last4  TEXT NOT NULL,               -- for masking only
    batch_id     TEXT NOT NULL,               -- batch of the first valid row, never changes
    external_ref TEXT NOT NULL,
    attributes   TEXT NOT NULL,               -- JSON of non-PII fields
    pii          TEXT NOT NULL,               -- JSON {field: fernet_token}
    UNIQUE (workflow_id, phone_hmac)
);

CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY,         -- arrival order
    event_id     TEXT NOT NULL UNIQUE,
    file_id      INTEGER NOT NULL REFERENCES ingested_files(file_id),
    line_number  INTEGER NOT NULL,
    event_type   TEXT,
    workflow_id  TEXT,
    call_id      TEXT,
    occurred_at  INTEGER,                     -- epoch microseconds (UTC); NULL if unparseable
    phone_hmac   TEXT,                        -- NULL if missing / invalid phone
    payload      TEXT NOT NULL                -- JSON, contains no PII
);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events ledger is append-only'); END;

CREATE TABLE IF NOT EXISTS event_line_issues (
    file_id     INTEGER NOT NULL REFERENCES ingested_files(file_id),
    line_number INTEGER NOT NULL,
    issue       TEXT NOT NULL CHECK (issue IN ('malformed', 'duplicate')),
    event_id    TEXT,
    PRIMARY KEY (file_id, line_number)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)  # we manage transactions explicitly
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(DDL)
    return conn

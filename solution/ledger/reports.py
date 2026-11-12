"""Builds the five report files from the database (always from everything ingested so far)."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .catalog import Disposition
from .derive import Event, build_facts, effective_disposition, lead_bucket
from .normalize import ist_date, ist_iso

BUCKETS = ["yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"]


def _bool(b: bool) -> str:
    return "true" if b else "false"


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def build_reports(conn: sqlite3.Connection, catalogs: dict[str, dict[str, Disposition]],
                  out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    leads = conn.execute(
        "SELECT lead_id, workflow_id, phone_hmac, phone_last4, batch_id, external_ref FROM leads"
    ).fetchall()
    lead_index = {(wf, ph): lid for lid, wf, ph, _, _, _ in leads}

    events = [
        Event(eid, etype, wf, cid, ts, ph, json.loads(payload))
        for eid, etype, wf, cid, ts, ph, payload in conn.execute(
            "SELECT event_id, event_type, workflow_id, call_id, occurred_at, phone_hmac, payload"
            " FROM events ORDER BY seq")
    ]
    facts = build_facts(events, lead_index, catalogs)

    def is_success(wf: str, code: str | None) -> bool:
        return code is not None and catalogs[wf][code].is_success

    # ---------- per lead ----------
    lead_rows = []
    batch_counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    daily: dict[tuple[str, str], dict] = {}
    for lead_id, wf, _, last4, batch_id, ref in leads:
        calls = [c for c in facts.calls_by_lead.get(lead_id, []) if c.counted]
        bucket = lead_bucket(calls, catalogs[wf])
        batch_counts[(wf, batch_id)][bucket] += 1

        latest_first = sorted(calls, key=lambda c: (c.initiated_at, c.call_id), reverse=True)
        current = None  # effective disposition of the most recent call that has one
        for c in latest_first:
            current = effective_disposition(c)
            if current:
                break
        ever_success = any(is_success(wf, effective_disposition(c)) for c in calls)
        lead_rows.append([
            wf, ref, batch_id, f"+91******{last4}", bucket, len(calls),
            sum(c.connected for c in calls),
            ist_iso(latest_first[0].initiated_at) if calls else "",
            current or "", _bool(ever_success),
        ])

        for c in calls:
            day = daily.setdefault((wf, ist_date(c.initiated_at)),
                                   {"total": 0, "connected": 0, "success": set()})
            day["total"] += 1
            day["connected"] += c.connected
            if is_success(wf, effective_disposition(c)):
                day["success"].add(lead_id)

    lead_rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    _write_csv(out_dir / "lead_report.csv",
               ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
                "connected_calls", "last_call_at", "current_disposition", "ever_success"],
               lead_rows)

    _write_csv(out_dir / "batch_summary.csv",
               ["workflow_id", "batch_id", "total_leads", *BUCKETS],
               [[wf, b, sum(cnt.values()), *(cnt[k] for k in BUCKETS)]
                for (wf, b), cnt in sorted(batch_counts.items())])

    _write_csv(out_dir / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate",
                "success_leads"],
               [[wf, d, v["total"], v["connected"], f"{v['connected'] / v['total']:.4f}",
                 len(v["success"])]
                for (wf, d), v in sorted(daily.items())])

    # ---------- rejections ----------
    rejections = conn.execute(
        "SELECT workflow_id, batch_id, row_number, reason FROM lead_rows WHERE outcome = 'rejected'"
        " ORDER BY workflow_id, batch_id, row_number").fetchall()
    _write_csv(out_dir / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"],
               [list(r) for r in rejections])

    # ---------- data quality ----------
    outcomes = dict(conn.execute("SELECT outcome, COUNT(*) FROM lead_rows GROUP BY outcome"))
    issues = dict(conn.execute("SELECT issue, COUNT(*) FROM event_line_issues GROUP BY issue"))
    ledger_size = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    quality = {
        "lead_rows_total": sum(outcomes.values()),
        "lead_rows_rejected": outcomes.get("rejected", 0),
        "rejections_by_reason": dict(sorted(Counter(r[3] for r in rejections).items())),
        "duplicate_lead_rows_merged": outcomes.get("merged", 0),
        "field_warnings": conn.execute("SELECT COALESCE(SUM(warnings), 0) FROM lead_rows")
                              .fetchone()[0],
        "events_total": ledger_size + sum(issues.values()),
        "malformed_events": issues.get("malformed", 0),
        "duplicate_events_dropped": issues.get("duplicate", 0),
        "orphan_events": facts.orphan_events,
        "unknown_disposition_events": facts.unknown_disposition_events,
    }
    (out_dir / "data_quality.json").write_text(json.dumps(quality, indent=2) + "\n",
                                               encoding="utf-8")

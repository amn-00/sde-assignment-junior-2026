"""Command line entry point.

    python solution/run.py ingest data/drop_1 [data/drop_2 ...]   # or: ingest data
    python solution/run.py report --out reports/
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .catalog import DEFAULT_SCHEMA_DIR, load_dispositions, load_schemas
from .crypto import Pii
from .db import connect
from .ingest import expand_drops, ingest_drop
from .reports import build_reports


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="ledger", description="Interaction ledger pipeline")
    ap.add_argument("--db", default="ledger.db", help="SQLite database path (default ledger.db)")
    ap.add_argument("--schemas", type=Path, default=DEFAULT_SCHEMA_DIR,
                    help="folder with <workflow>.json schemas and dispositions.csv")
    sub = ap.add_subparsers(dest="command", required=True)
    p_ing = sub.add_parser("ingest", help="ingest one or more drops (in the order given)")
    p_ing.add_argument("paths", nargs="+")
    p_rep = sub.add_parser("report", help="write the five report files")
    p_rep.add_argument("--out", type=Path, default=Path("reports"))
    args = ap.parse_args(argv)

    conn = connect(args.db)
    started = time.perf_counter()
    try:
        if args.command == "ingest":
            schemas = load_schemas(args.schemas)
            pii = Pii()
            for drop in expand_drops(args.paths):
                stats = ingest_drop(conn, pii, schemas, drop)
                print(f"[ledger] {drop}: {stats['files_ingested']} file(s) ingested, "
                      f"{stats['files_skipped']} already ingested (skipped)")
        else:
            build_reports(conn, load_dispositions(args.schemas), args.out)
            print(f"[ledger] reports written to {args.out}/")
    finally:
        conn.close()
    print(f"[ledger] done in {time.perf_counter() - started:.2f}s")

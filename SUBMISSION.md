# Submission

## Candidate
- **Name:** Aman Chaudhary
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** ~8 hours in total (building with Claude on claude.ai, then
  reviewing, testing and fixing performance with Claude Code)

## How to run
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

python solution/run.py ingest data/drop_1
python solution/run.py ingest data/drop_2      # or both at once: python solution/run.py ingest data
python solution/run.py report --out reports/

pytest                                         # 69 tests, < 1s
```
Options: `--db <path>` (default `ledger.db`), `--schemas <dir>` (default `assignment-a-data/schemas`).
On first run a PII key is generated into `.env` (gitignored), or set `AU_PII_KEY` yourself.
Keep the same key for a database: phone HMACs depend on it.

## Design

Code lives in `solution/ledger/`:

| File | What it does |
|---|---|
| `normalize.py` | Pure parsers: headers, phone, date, decimal, enum, timestamps |
| `catalog.py` | Loads workflow schemas and the disposition catalogue |
| `crypto.py` | PII: HMAC for phone matching, Fernet encryption for stored PII |
| `db.py` | SQLite schema |
| `ingest.py` | Finds drops/files, one transaction per file, skips files already ingested |
| `leads.py` | Lead rows: header mapping, validation, rejection, merging |
| `events.py` | Event lines into the ledger: malformed and duplicate detection |
| `derive.py` | Pure rules: linking, calls, effective disposition, buckets |
| `reports.py` | Writes the five report files |

**Core idea: ingestion only records facts; everything derived is computed at report time.**
Ingest stores leads and every unique event. It does *not* decide whether an event is an
orphan. `report` links events to leads using everything in the database at that moment. So an
event that arrived before its lead counts automatically once the lead shows up (seed 42:
1,123 orphans after drop 1, 462 after drop 2), and nothing ever has to be re-linked or edited.

### Database schema (SQLite)
```
ingested_files   (file_id, kind, workflow_id, file_name, sha256)   UNIQUE(kind, workflow_id, file_name, sha256)
lead_rows        (file_id, row_number, workflow_id, batch_id, outcome[created|merged|rejected], reason, warnings)
leads            (lead_id, workflow_id, phone_hmac, phone_last4, batch_id, external_ref, attributes JSON, pii JSON)
                 UNIQUE(workflow_id, phone_hmac)
events           (seq, event_id UNIQUE, file_id, line_number, event_type, workflow_id, call_id,
                  occurred_at [epoch µs UTC], phone_hmac, payload JSON)        <- the ledger
event_line_issues(file_id, line_number, issue[malformed|duplicate], event_id)
```
- **Ledger is append-only, enforced by the database:** triggers abort any `UPDATE`/`DELETE` on
  `events`. Overrides are just more rows; the effective disposition is computed, never stored.
- **Every count in `data_quality.json` is derived from tables**, not from counters kept in
  memory. `lead_rows` keeps one row per non-blank lead row with its outcome, and
  `event_line_issues` keeps malformed/duplicate lines. That makes the numbers stay correct
  across re-runs and partial runs.
- `attributes` is JSON because each workflow has different fields; the schema files stay the
  single source of truth. `external_ref` is pulled out into a column for sorting.
- Timestamps are stored as integer microseconds (UTC), so ordering is exact and
  timezone-independent. Conversion to IST happens only when writing reports.

### PII
- Phone → **keyed HMAC-SHA256** of the canonical `+91XXXXXXXXXX`. Deterministic, so leads and
  events join on it, but without the key you can't brute-force it (only ~4 billion valid mobile
  numbers exist, so a plain unsalted hash would be reversible).
- PII fields (`customer_name`, `phone`) → **Fernet-encrypted** in `leads.pii`, so the real
  values can be recovered by someone holding the key (e.g. the dialler).
- Only the last 4 digits are stored in clear, for `phone_masked`.
- Event phones are stored only as HMACs; raw event lines are not kept.
- A test scans the raw DB files and checks no names or phone numbers appear.

### Idempotency and incremental ingest
- Each file is identified by `(kind, workflow, file name, sha256)`. An already-recorded file
  is skipped, so re-running `ingest` on a drop changes nothing.
- Each file is ingested in **one transaction** together with its `ingested_files` row. A crash
  mid-file rolls everything back, and the next run redoes that file cleanly. No half-ingested files.
- Drop 2 never re-processes drop 1's files (truly incremental). Reports are recomputed from the
  whole DB each time; that's cheap (≈17s at large scale on my laptop) and makes "drop by drop == all at
  once" hold by construction.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Link events to leads at report time, not at ingest | Late leads link automatically; ledger never mutates; orphan count always reflects final state | Store `lead_id` on events and re-link orphans after each lead ingest (faster reports, more moving parts) |
| File identity = name + content hash | Re-running the same drop (even from another path) is a no-op | Path only: misses copied drops; hash only: two identical files would collide |
| A changed file with the same name is treated as a new upload | Its rows go through normal merge rules, nothing is silently lost | Reject it, or replace the old batch (would need deleting rows) |
| `ingest data` (folder of drops) = ingest drops in natural-sorted order | This is how "everything at once" is run | Require listing each drop |
| Header normalisation also trims after collapsing separators | `phone_` → `phone` still matches | Literal spec order (`phone_` → `"phone "`, no match) |
| If two columns map to the same field, the first one wins | Deterministic, simple | Last wins / reject file |
| Required non-phone field present but unparseable → reject as `invalid:<field>` | Spec only defines missing/invalid_phone; doesn't happen with current schemas | Treat as `missing_required` |
| Defaults applied when the lead is created | Equivalent to "after merging", since later rows only overwrite with non-null values | Apply at read time |
| `ref` and other fields can be overwritten by a later duplicate | Spec says last write wins on every non-null field | Freeze the reference field |
| Dates: accept 1–2 digit day/month, 3-letter English month names | Lenient on padding without guessing formats | Strict zero-padding only |
| Amounts rounded half-up to 2 places, stored as text | Exact decimals, no float drift | Float |
| Tie on `occurred_at` between overrides/sets → higher `event_id` wins | Deterministic regardless of ingest order | Arrival order (differs between split and all-at-once runs) |
| Several `call.initiated` for one call → earliest time | Call's date is when it was first placed | Latest |
| Event with unparseable `occurred_at` is kept but has no time; a `call.initiated` without time doesn't count as a call | Spec defines malformed narrowly (bad JSON / no event_id) | Count it as malformed |
| Override links via `(workflow_id, call_id)` | Same call_id in another workflow shouldn't cross-link | call_id only |
| Unknown disposition counted only for linked events | Spec order: orphan check comes before unknown disposition | Count regardless |
| `connectivity_rate` formatted with Python `f"{x:.4f}"` | Matches the README example (`0.5625`) | Decimal half-up |
| `batch_summary` lists batches that have ≥1 lead | Leads are what's being summarised | Also list all-rejected batches with zeros |
| `lead_report` ties on `external_ref` broken by batch, then masked phone | Stable output | — |
| SQLite, not Postgres | Zero setup, runs from a clean clone; schema is portable | Postgres via docker-compose |

## Failure modes considered
- **Re-running a drop:** file-level skip, so nothing changes (tested).
- **Crash mid-ingest:** per-file transaction; rerun picks up where it stopped.
- **Drops out of order / events before leads:** handled by report-time linking. Note: lead
  `batch_id` and merge order still follow ingest order, as the spec requires.
- **Duplicate events within or across drops:** `event_id` is `UNIQUE`; first copy is kept,
  later copies are recorded as duplicates.
- **Malformed lines** (truncated JSON, non-object JSON, missing/empty `event_id`): counted
  and skipped. Blank lines are ignored and not counted.
- **Dirty lead files:** BOM, blank rows, short rows (missing cells = missing values), extra
  columns, renamed/re-ordered headers.
- **Unknown workflow folder:** skipped with a warning.
- **Override for an unknown call, or for a call that is itself orphaned:** orphan.
- **Lost PII key:** existing HMACs can't be reproduced. Documented; in production the key
  would live in a KMS.

**Not handled:**
- Concurrent `ingest` processes on the same DB. SQLite serialises them, but there's no extra
  locking beyond that.
- Deleting or correcting a batch after it was ingested.
- Key rotation.
- Very large single files: each file is read into memory (fine up to tens of MB).

## Testing
`pytest` runs 69 tests in < 1s:
- **Parsers:** every phone format in the generator plus invalid ones; all four date formats,
  impossible dates, leap years; currency prefixes, commas, negatives; enums; header
  normalisation; UTC/offset/naive timestamps, including an IST date that crosses midnight.
- **Rules:** effective disposition (override beats later set, latest override by time not
  arrival); every bucket branch, including "override reopens a completed lead"; override
  linking via call_id; orphan → linked once the lead exists; unknown codes on orphans.
- **End to end** on small hand-written drops: rejection order, row numbering with blank rows,
  merge / last-write-wins / defaults, batch_id permanence, every `data_quality` count,
  re-ingest no-op, split == all-at-once, no plaintext PII in the DB files, append-only triggers.

**Checked outside the test suite (not committed):** I built an independent oracle that re-runs
`generate_data.py` with the same seed, records the ground truth for every lead and call, and
compares it to the reports. For seeds 42, 7, 1234 and 99999: every row of `lead_report.csv`
and `daily_overview.csv` matches, `rejected`/`orphan`/`unknown`/`duplicate` counts match, and
drop-by-drop, re-ingest and all-at-once reports are byte-identical. Re-checked later on three
more seeds (2026, 31337, 4242): 0 mismatches in `lead_report.csv`, `daily_overview.csv` and
`batch_summary.csv`, and rejected/orphan/unknown counts match.

## Results
- **Small (seed 42):** full ingest + report in ~2s on my laptop (Windows).
- **Large (seed 42, ~140k leads, ~630k event lines):** **over the 2-minute target on my laptop.**

  | | Drop 1 | Drop 2 | Report | Total |
  |---|---|---|---|---|
  | Linux sandbox (first version) | 15.5s | 17.9s | 7.8s | ~41s |
  | My Windows laptop, first version | 666s | not run | – | – |
  | My Windows laptop, after the index fix | 140s | 144s | 17s | **~5 min** |

  On the laptop, event ingest got slower with every file (0.3s for the first file, 29s for the
  later ones). Ingesting into an in-memory SQLite took 45s for drop 1, so the Python code
  was fine and the cost was disk writes. The cause was an index on
  `events (workflow_id, phone_hmac)` that no query used: the report reads the whole ledger
  in one scan. Phone HMACs are random, so every insert touched a random index page, and each
  commit wrote more of the index to disk as it grew. Removing it made drop 1 4.7× faster with
  byte-identical reports, and per-file time is now flat (~1.7s). Timings vary between runs on
  the laptop (one drop 1 run took 346s while other disk activity was going on).

  What's left is the `event_id` UNIQUE index (needed for dedupe), rewritten on every per-file
  commit. Committing once per drop instead of once per file would likely get under 2 minutes,
  but then a crash would redo the whole drop instead of one file. I kept per-file transactions
  because correctness and recoverability matter more than a stretch goal.
- The README's sample values match my output for seed 42: `motor_renewal, 2026-10-01` has
  rate `0.5625`, and `MOT/2026/100000` has last call `2026-10-14T17:55:24+05:30`.
- **Surprising in the data:**
  - After drop 1 there are 1,123 orphans; after drop 2 only 462 in total (including drop 2's
    own). Most early orphans were just leads that hadn't been uploaded yet.
  - Events for *rejected* leads (missing name/ref) stay orphans forever. That's correct per
    spec, but in real life ops would want those surfaced, because we're calling people whose
    upload we rejected.
  - Overrides land up to 72h after the call, so they often arrive a drop later and change a
    lead's bucket after the fact.

## If I had another day
1. **Quarantine report:** list orphan events with a reason (no such lead / invalid phone /
   rejected lead / unknown call), so ops can act on them, not just count them.
2. **Store linked lead_id incrementally** (materialised `event_links` table, re-linked only
   for new leads) so report time stays flat as the ledger grows.
3. **Move the aggregation into SQL views**, so dashboards can query directly.
4. **Postgres** with `COPY` for bulk loads and row-level locking for concurrent ingest.
5. **Production at 100k leads/day:** a scheduled job per drop (Airflow/Temporal) with the
   same per-file idempotency key; failed files retried automatically; backfill = re-ingest
   older drops into a fresh DB and diff the reports; key in a KMS with rotation via
   re-encrypt + dual-HMAC for a transition window.

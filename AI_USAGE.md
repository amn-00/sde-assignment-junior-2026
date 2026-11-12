# AI Usage Disclosure

## Tools Used
- **Claude (Anthropic, claude.ai):** used to build the solution.
- **Claude Code (VS Code extension):** used for reviewing the code against the spec, testing,
  simplifying, and finding a performance problem.

## What I Used Them For

**Building the solution (claude.ai):**
- Compared the two assignments. I chose Assignment A.
- Claude wrote most of the code in `solution/ledger/` (ingestion, parsing, normalisation,
  linking, reporting) and the tests in `solution/tests/`.
- Claude built a ground-truth check that re-runs the data generator and compares every lead and
  daily row in the reports (seeds 42, 7, 1234, 99999). Not committed.
- Drafted `SUBMISSION.md` with Claude's help.

**Review, simplification and debugging (Claude Code):**
- Reviewed the implementation against the specification, rule by rule.
- Simplified several parts for readability without changing behaviour:
  - `ingest.py`: replaced lambda callbacks with a straightforward leads/events branch.
  - `leads.py`: replaced a generator that treated the header as row 0 with a simple loop.
  - `normalize.py`: simplified the epoch-microsecond conversion.
  - `reports.py`: replaced a walrus expression inside a generator with a readable for-loop.
  - `crypto.py`: the PII key file `.env` is now always at the repository root.
- Investigated why the large dataset was slow on my laptop and removed an unused index (details
  below).
- Re-checked the reports against generator ground truth on three new seeds.

## What I Decided and Did Myself
- Chose Assignment A after comparing both assignments.
- Asked for the solution to be reviewed against the spec and simplified wherever it was more
  complicated than it needed to be.
- Reviewed the suggested changes and checked the before/after results instead of accepting
  them blindly.
- Ran the pipeline and the test suite on my own Windows laptop (Python 3.12) instead of relying
  on the earlier sandbox numbers. My run of the large dataset (666s for drop 1 alone) showed
  the performance claim in `SUBMISSION.md` was wrong, and I raised it.
- After the fix the large dataset still took about five minutes. I decided to keep per-file
  transactions instead of switching to one commit per drop: recoverability matters more than
  the two-minute stretch target. I chose to report the real numbers honestly.

## Where AI Suggestions Were Wrong or Needed Correction
- **Incorrect performance claim:** the first `SUBMISSION.md` reported ~41 seconds for the large
  dataset, measured in a Linux sandbox. On my Windows laptop, drop 1 alone took 666 seconds.
  The numbers are now corrected.
- **Incorrect first diagnosis:** Claude Code first raised SQLite's cache size. Measuring showed
  no improvement, so it was reverted. Per-file timings and an in-memory test then showed the
  real cause: an index on `events (workflow_id, phone_hmac)` that no query used. Removing it
  took drop 1 from 666s to 140s, with byte-identical reports.
- **Timing mistake:** when Claude Code stopped one of its test runs, the Python process kept
  running on Windows and spoiled one measurement. It noticed this and repeated the measurement.
- **Bugs found and fixed while the code was written on claude.ai:**
  - Event files were split with `str.splitlines()`, which also splits on Unicode line
    separators that can appear inside JSON strings. Now splits on `\n` only.
  - The "already ingested?" check ran before the transaction, so two processes could race.
    Moved it inside `BEGIN IMMEDIATE`.
  - Following the header rule literally meant `phone_` did not match `phone`. Added a final
    trim and documented the decision.

## How the Final Implementation Was Verified
- `pytest`: all 69 tests pass on my Windows laptop (Python 3.12).
- Ground-truth check on seeds the code had never seen (2026, 31337, 4242): 0 mismatches in
  `lead_report.csv`, `daily_overview.csv` and `batch_summary.csv`; rejected, orphan and
  unknown-disposition counts match.
- Reports compared byte for byte before and after the refactor and the index removal (seeds 42
  and 7, and the large dataset): identical.
- Re-ingesting, and drop-by-drop vs all-at-once: byte-identical reports.
- The README's example values (`0.5625` and `2026-10-14T17:55:24+05:30`) appear in my output
  for seed 42.

**Overall:** Claude did most of the writing. I directed the work, chose what to fix and what to
leave, tested it on my own machine, and made the trade-off decisions. Where the AI's claims didn't
hold up on my machine, I made sure they were corrected instead of passing them on.

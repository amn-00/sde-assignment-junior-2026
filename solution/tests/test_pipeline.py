"""End-to-end tests on tiny hand-written drops."""
import csv
import json
import sqlite3

import pytest

from ledger.cli import main

LEADS_1 = """customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language
Aarav Menon,+91 98765 43210,MOT/1,01-Dec-2026,"₹27,554",
,9000000001,MOT/2,,,
Bad Phone,12345,MOT/3,,,
,,,,,
Priya Rao,8000000002,MOT/4,31/02/2026,Rs. 500,Hindi
"""
# later batch: renamed + reordered columns, an extra column, a duplicate of Aarav
LEADS_2 = """Mobile No,Insured Name,Policy No,Premium,Language,agent_notes
09876543210,Aarav M.,MOT/1,"30,000",HI,VIP
7000000003,Meera Das,MOT/5,,,
"""


def events(*rows):
    return "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n"


def e(eid, etype, call_id, t, phone=None, **payload):
    d = {"event_id": eid, "event_type": etype, "workflow_id": "motor_renewal",
         "call_id": call_id, "occurred_at": t, "payload": payload}
    if phone:
        d["phone"] = phone
    return d


EVENTS_1 = events(
    e("e1", "call.initiated", "c1", "2026-10-01T04:00:00Z", "9876543210"),
    e("e2", "call.connected", "c1", "2026-10-01T04:00:10Z", "+919876543210"),
    e("e3", "disposition.set", "c1", "2026-10-01 09:35:00", "9876543210",
      disposition_code="callback_requested"),
    e("e1", "call.initiated", "c1", "2026-10-01T04:00:00Z", "9876543210"),     # duplicate
    '{"event_id": "e9", "event_ty',                                            # malformed
    e("e4", "call.initiated", "c2", "2026-10-02T10:00:00+05:30", "7000000003"),  # lead in drop 2
    e("e5", "call.telephony_failed", "c2", "2026-10-02T10:00:05+05:30", "7000000003",
      reason="sip_503"),
    "",
)
EVENTS_2 = events(
    e("e6", "disposition.override", "c1", "2026-10-03T00:00:00Z", overridden_by="sup",
      disposition_code="renewed"),
    e("e7", "call.initiated", "c3", "2026-10-03T00:00:00Z", "6000000009"),     # never uploaded
    e("e8", "disposition.set", "c1", "2026-10-01T05:00:00Z", "9876543210",
      disposition_code="RENEWD"),                                               # unknown code
)


@pytest.fixture
def drops(tmp_path, monkeypatch):
    monkeypatch.setenv("AU_PII_KEY", "test-key")
    root = tmp_path / "data"
    for drop, leads, evs, batch in [("drop_1", LEADS_1, EVENTS_1, "batch_001"),
                                    ("drop_2", LEADS_2, EVENTS_2, "batch_002")]:
        (root / drop / "leads" / "motor_renewal").mkdir(parents=True)
        (root / drop / "events").mkdir()
        (root / drop / "leads" / "motor_renewal" / f"{batch}.csv").write_text(leads, encoding="utf-8")
        (root / drop / "events" / "events_0001.jsonl").write_text(evs, encoding="utf-8")
    return root


def run(db, *args):
    main(["--db", str(db), *args])


def read_reports(out):
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(out.iterdir())}


def lead_report(out):
    return {r["external_ref"]: r for r in csv.DictReader(open(out / "lead_report.csv"))}


def test_end_to_end_rules(drops, tmp_path):
    db, out = tmp_path / "x.db", tmp_path / "out"
    run(db, "ingest", str(drops / "drop_1"))
    run(db, "ingest", str(drops / "drop_2"))
    run(db, "report", "--out", str(out))

    q = json.loads((out / "data_quality.json").read_text())
    assert q == {
        "lead_rows_total": 6,            # blank row not counted
        "lead_rows_rejected": 2,
        "rejections_by_reason": {"invalid_phone": 1, "missing_required:customer_name": 1},
        "duplicate_lead_rows_merged": 1,
        "field_warnings": 2,             # bad date + 'Hindi' on MOT/4
        "events_total": 10,              # empty line not counted
        "malformed_events": 1,
        "duplicate_events_dropped": 1,
        "orphan_events": 1,              # c3 (e4/e5 linked once MOT/5 arrived)
        "unknown_disposition_events": 1,
    }
    rej = list(csv.DictReader(open(out / "rejections.csv")))
    assert [(r["row_number"], r["reason"]) for r in rej] == [
        ("2", "missing_required:customer_name"), ("3", "invalid_phone")]

    leads = lead_report(out)
    aarav = leads["MOT/1"]
    assert aarav["batch_id"] == "batch_001"          # first row's batch is permanent
    assert aarav["phone_masked"] == "+91******3210"
    assert aarav["bucket"] == "completed"            # override -> renewed (terminal)
    assert aarav["current_disposition"] == "renewed"
    assert aarav["ever_success"] == "true"
    assert aarav["last_call_at"] == "2026-10-01T09:30:00+05:30"
    assert leads["MOT/5"]["bucket"] == "in_progress_telephony_failed"
    assert leads["MOT/5"]["batch_id"] == "batch_002"
    assert leads["MOT/4"]["bucket"] == "yet_to_reach_out"

    with sqlite3.connect(db) as conn:
        attrs = {r[0]: json.loads(r[1]) for r in conn.execute(
            "SELECT external_ref, attributes FROM leads")}
    assert attrs["MOT/1"]["premium_amount"] == "30000.00"      # merged: last write wins
    assert attrs["MOT/1"]["preferred_language"] == "hi"
    assert attrs["MOT/1"]["policy_expiry"] == "2026-12-01"     # null in dup -> kept
    assert attrs["MOT/4"]["preferred_language"] == "en"        # default after invalid value


def test_reingest_changes_nothing_and_split_equals_all_at_once(drops, tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    run(a, "ingest", str(drops / "drop_1"))
    run(a, "ingest", str(drops / "drop_2"))
    run(a, "report", "--out", str(tmp_path / "ra"))
    run(a, "ingest", str(drops))                     # everything again
    run(a, "report", "--out", str(tmp_path / "ra2"))
    run(b, "ingest", str(drops))                     # all at once into a fresh db
    run(b, "report", "--out", str(tmp_path / "rb"))
    assert read_reports(tmp_path / "ra") == read_reports(tmp_path / "ra2") == read_reports(tmp_path / "rb")


def test_pii_not_stored_in_plaintext(drops, tmp_path):
    db = tmp_path / "x.db"
    run(db, "ingest", str(drops))
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("x.db*"))
    for needle in [b"Aarav", b"Meera", b"9876543210", b"7000000003", b"6000000009"]:
        assert needle not in raw


def test_ledger_is_append_only(drops, tmp_path):
    db = tmp_path / "x.db"
    run(db, "ingest", str(drops))
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE events SET event_type = 'x'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM events")

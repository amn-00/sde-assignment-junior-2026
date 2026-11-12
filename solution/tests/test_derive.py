from ledger.catalog import Disposition
from ledger.derive import Call, Event, build_facts, effective_disposition, lead_bucket

CATALOG = {
    "renewed": Disposition(is_terminal=True, is_success=True),
    "callback_requested": Disposition(is_terminal=False, is_success=False),
    "not_interested": Disposition(is_terminal=True, is_success=False),
}
WF = "motor_renewal"


def call(**kw) -> Call:
    return Call(call_id=kw.pop("call_id", "c1"), lead_id=1, initiated_at=kw.pop("t", 100), **kw)


# ---------- effective disposition ----------

def test_no_disposition():
    assert effective_disposition(call()) is None


def test_latest_set_wins():
    c = call(sets=[(10, "e1", "callback_requested"), (20, "e2", "renewed")])
    assert effective_disposition(c) == "renewed"


def test_override_beats_later_set():
    c = call(sets=[(50, "e2", "renewed")], overrides=[(10, "e1", "not_interested")])
    assert effective_disposition(c) == "not_interested"


def test_latest_override_by_time_not_arrival():
    c = call(overrides=[(30, "e9", "renewed"), (20, "e5", "not_interested")])
    assert effective_disposition(c) == "renewed"


# ---------- buckets ----------

def test_bucket_no_calls():
    assert lead_bucket([], CATALOG) == "yet_to_reach_out"


def test_bucket_calls_without_initiated_do_not_count():
    assert lead_bucket([call(t=None, connected=True)], CATALOG) == "yet_to_reach_out"


def test_bucket_any_terminal_is_completed_even_if_others_failed():
    calls = [call(call_id="a", telephony_failed=True),
             call(call_id="b", sets=[(1, "e", "not_interested")])]
    assert lead_bucket(calls, CATALOG) == "completed"


def test_bucket_all_telephony_failed():
    calls = [call(call_id="a", telephony_failed=True), call(call_id="b", telephony_failed=True)]
    assert lead_bucket(calls, CATALOG) == "in_progress_telephony_failed"


def test_bucket_dialed_when_only_some_failed():
    calls = [call(call_id="a", telephony_failed=True), call(call_id="b")]
    assert lead_bucket(calls, CATALOG) == "in_progress_dialed"


def test_bucket_override_to_non_terminal_reopens_lead():
    c = call(sets=[(1, "e1", "renewed")], overrides=[(2, "e2", "callback_requested")])
    assert lead_bucket([c], CATALOG) == "in_progress_dialed"


# ---------- linking ----------

def ev(eid, etype, call_id="c1", phone="P1", t=100, **payload):
    return Event(eid, etype, WF, call_id, t, phone, payload)


def test_override_links_through_call_id_and_unknown_codes_are_ignored():
    events = [
        ev("e1", "call.initiated"),
        ev("e2", "call.connected"),
        ev("e3", "disposition.set", t=200, disposition_code="renewed"),
        ev("e4", "disposition.override", phone=None, t=300, disposition_code="not_interested"),
        ev("e5", "disposition.set", t=400, disposition_code="RENEWD"),        # unknown
        ev("e6", "call.initiated", call_id="c2", phone="NOBODY"),             # orphan
        ev("e7", "disposition.override", call_id="c2", phone=None,
           disposition_code="renewed"),                                        # orphan via call
    ]
    facts = build_facts(events, {(WF, "P1"): 1}, {WF: CATALOG})
    assert facts.orphan_events == 2
    assert facts.unknown_disposition_events == 1
    [c] = facts.calls_by_lead[1]
    assert c.connected and effective_disposition(c) == "not_interested"


def test_orphan_becomes_linked_once_lead_exists():
    events = [ev("e1", "call.initiated")]
    assert build_facts(events, {}, {WF: CATALOG}).orphan_events == 1
    facts = build_facts(events, {(WF, "P1"): 7}, {WF: CATALOG})
    assert facts.orphan_events == 0 and len(facts.calls_by_lead[7]) == 1


def test_unknown_disposition_on_orphan_counts_as_orphan_only():
    events = [ev("e1", "disposition.set", phone="NOBODY", disposition_code="??")]
    facts = build_facts(events, {}, {WF: CATALOG})
    assert (facts.orphan_events, facts.unknown_disposition_events) == (1, 0)

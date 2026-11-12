"""Derived facts from the ledger: linking, calls, effective disposition, buckets.

Pure functions over plain data so the rules can be unit-tested without a database.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Disposition

NO_TS = -(2**63)  # sorts before any real timestamp


@dataclass
class Event:
    event_id: str
    event_type: str | None
    workflow_id: str | None
    call_id: str | None
    occurred_at: int | None   # epoch microseconds
    phone_hmac: str | None
    payload: dict


@dataclass
class Call:
    call_id: str
    lead_id: int
    initiated_at: int | None = None
    connected: bool = False
    telephony_failed: bool = False
    sets: list[tuple[int, str, str]] = field(default_factory=list)       # (ts, event_id, code)
    overrides: list[tuple[int, str, str]] = field(default_factory=list)

    @property
    def counted(self) -> bool:
        """A call only counts if its call.initiated event is present."""
        return self.initiated_at is not None


def effective_disposition(call: Call) -> str | None:
    """Latest override by occurred_at; else latest set; else None. Ties broken by event_id."""
    if call.overrides:
        return max(call.overrides)[2]
    if call.sets:
        return max(call.sets)[2]
    return None


def lead_bucket(calls: list[Call], catalog: dict[str, Disposition]) -> str:
    calls = [c for c in calls if c.counted]
    if not calls:
        return "yet_to_reach_out"
    for c in calls:
        d = effective_disposition(c)
        if d is not None and catalog[d].is_terminal:
            return "completed"
    if all(c.telephony_failed for c in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


@dataclass
class LedgerFacts:
    calls_by_lead: dict[int, list[Call]]
    orphan_events: int
    unknown_disposition_events: int


def build_facts(events: list[Event], lead_index: dict[tuple[str, str], int],
                catalogs: dict[str, dict[str, Disposition]]) -> LedgerFacts:
    """lead_index: (workflow_id, phone_hmac) -> lead_id."""
    # Pass 1: link every event that carries a phone; remember which lead each call belongs to.
    call_lead: dict[tuple[str, str], int] = {}
    linked: list[tuple[Event, int]] = []
    overrides: list[Event] = []
    orphans = 0
    for e in events:
        if e.event_type == "disposition.override":
            overrides.append(e)
            continue
        lead_id = lead_index.get((e.workflow_id, e.phone_hmac)) if e.phone_hmac else None
        if lead_id is None:
            orphans += 1
            continue
        linked.append((e, lead_id))
        if e.call_id:
            call_lead.setdefault((e.workflow_id, e.call_id), lead_id)

    # Pass 2: overrides have no phone; they link through the call's other events.
    for e in overrides:
        lead_id = call_lead.get((e.workflow_id, e.call_id))
        if lead_id is None:
            orphans += 1
        else:
            linked.append((e, lead_id))

    # Pass 3: fold linked events into calls.
    calls: dict[tuple[int, str], Call] = {}
    unknown = 0
    for e, lead_id in linked:
        if not e.call_id:
            continue
        call = calls.setdefault((lead_id, e.call_id), Call(e.call_id, lead_id))
        ts = e.occurred_at if e.occurred_at is not None else NO_TS
        if e.event_type == "call.initiated":
            if e.occurred_at is not None and (call.initiated_at is None
                                              or e.occurred_at < call.initiated_at):
                call.initiated_at = e.occurred_at
        elif e.event_type == "call.connected":
            call.connected = True
        elif e.event_type == "call.telephony_failed":
            call.telephony_failed = True
        elif e.event_type in ("disposition.set", "disposition.override"):
            code = e.payload.get("disposition_code")
            if code not in catalogs.get(e.workflow_id, {}):
                unknown += 1
                continue
            target = call.sets if e.event_type == "disposition.set" else call.overrides
            target.append((ts, e.event_id, code))

    by_lead: dict[int, list[Call]] = {}
    for call in calls.values():
        by_lead.setdefault(call.lead_id, []).append(call)
    return LedgerFacts(by_lead, orphans, unknown)

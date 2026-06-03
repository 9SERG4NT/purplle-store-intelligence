"""Reconstruct per-visitor sessions from raw events (the shared read-model).

A *session* is one visitor_id. Re-entries reuse the same visitor_id upstream,
so grouping by visitor_id is exactly the "session is the unit, re-entries must
not double-count" requirement for the funnel. metrics / funnel / heatmap all
build on `load_sessions` so they can never disagree with each other.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.models import Event, PosTransaction

BILLING_ZONE = "BILLING"


@dataclass
class SessionAgg:
    visitor_id: str
    is_staff: bool = False
    has_entry: bool = False
    zones_visited: set[str] = field(default_factory=set)   # non-billing named zones
    reached_billing: bool = False
    abandoned: bool = False
    served: bool = False                                   # queue_completed (direct purchase signal)
    converted: bool = False
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    billing_times: list[datetime] = field(default_factory=list)
    zone_dwell_ms: dict[str, int] = field(default_factory=dict)  # zone -> max dwell
    gender: str | None = None
    age_bucket: str | None = None


@dataclass
class WindowData:
    sessions: dict[str, SessionAgg]
    purchases: int
    queue_depths: list[tuple[datetime, int]]  # (ts, depth) for billing-join events
    zone_meta: dict[str, dict]                 # zone_id -> {name, is_revenue, last_visit}

    @property
    def customers(self) -> list[SessionAgg]:
        return [s for s in self.sessions.values() if not s.is_staff]

    @property
    def staff_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.is_staff)


def _as_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def load_sessions(
    db: DbSession, store_id: str, start: datetime, end: datetime
) -> WindowData:
    conv_window = timedelta(minutes=get_settings().conversion_window_minutes)

    events = db.execute(
        select(Event)
        .where(Event.store_id == store_id, Event.ts >= start, Event.ts < end)
        .order_by(Event.ts)
    ).scalars().all()

    # POS txns that could convert a billing visit inside this window
    txns = db.execute(
        select(PosTransaction.ts)
        .where(PosTransaction.store_id == store_id,
               PosTransaction.ts >= start,
               PosTransaction.ts < end + conv_window)
    ).scalars().all()
    txn_times = sorted(_as_utc(t) for t in txns)

    sessions: dict[str, SessionAgg] = {}
    queue_depths: list[tuple[datetime, int]] = []
    zone_meta: dict[str, dict] = {}

    for e in events:
        if e.zone_id and e.zone_type != "BILLING":
            zm = zone_meta.setdefault(e.zone_id, {"name": e.zone_id, "is_revenue": None, "last_visit": None})
            if e.zone_name:
                zm["name"] = e.zone_name
            if e.is_revenue_zone is not None:
                zm["is_revenue"] = e.is_revenue_zone
            if e.event_type == "ZONE_ENTER" and not e.is_staff:
                ze = _as_utc(e.ts)
                if zm["last_visit"] is None or ze > zm["last_visit"]:
                    zm["last_visit"] = ze
        s = sessions.get(e.visitor_id)
        if s is None:
            s = SessionAgg(visitor_id=e.visitor_id, is_staff=e.is_staff)
            sessions[e.visitor_id] = s
        s.is_staff = s.is_staff or e.is_staff  # staff flag is sticky
        ets = _as_utc(e.ts)
        s.first_ts = ets if s.first_ts is None else min(s.first_ts, ets)
        s.last_ts = ets if s.last_ts is None else max(s.last_ts, ets)

        if e.event_type in ("ENTRY", "REENTRY"):
            s.has_entry = True
        if e.gender and not s.gender:
            s.gender = e.gender
        if e.age_bucket and not s.age_bucket:
            s.age_bucket = e.age_bucket
        is_billing = (
            e.zone_type == "BILLING" or e.zone_id == BILLING_ZONE
            or e.event_type in ("BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "QUEUE_COMPLETED")
        )
        if is_billing:
            s.reached_billing = True
            s.billing_times.append(ets)
            if e.event_type == "BILLING_QUEUE_ABANDON" or e.abandoned is True:
                s.abandoned = True
            if e.event_type == "QUEUE_COMPLETED" and not e.abandoned:
                s.served = True  # got served at the counter -> a purchase, no POS lookup needed
            if e.queue_depth is not None:
                queue_depths.append((ets, e.queue_depth))
        elif e.event_type == "ZONE_ENTER" and e.zone_id:
            s.zones_visited.add(e.zone_id)
        if e.event_type in ("ZONE_EXIT", "ZONE_DWELL") and e.zone_id and e.zone_type != "BILLING":
            s.zone_dwell_ms[e.zone_id] = max(s.zone_dwell_ms.get(e.zone_id, 0), e.dwell_ms)

    # conversion: served at the counter (queue_completed) OR billing presence within
    # the POS correlation window before a transaction
    for s in sessions.values():
        if s.served:
            s.converted = True
            continue
        for bt in s.billing_times:
            if any(bt <= tt <= bt + conv_window for tt in txn_times):
                s.converted = True
                break

    purchases = sum(1 for t in txn_times if t < end)  # tail beyond `end` is only for conversion
    return WindowData(sessions=sessions, purchases=purchases, queue_depths=queue_depths,
                      zone_meta=zone_meta)

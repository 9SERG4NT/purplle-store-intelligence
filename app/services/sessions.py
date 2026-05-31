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
    converted: bool = False
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    billing_times: list[datetime] = field(default_factory=list)
    zone_dwell_ms: dict[str, int] = field(default_factory=dict)  # zone -> max dwell


@dataclass
class WindowData:
    sessions: dict[str, SessionAgg]
    purchases: int
    queue_depths: list[tuple[datetime, int]]  # (ts, depth) for billing-join events

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

    for e in events:
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
        is_billing = e.zone_id == BILLING_ZONE or e.event_type.startswith("BILLING_")
        if is_billing:
            s.reached_billing = True
            s.billing_times.append(ets)
            if e.event_type == "BILLING_QUEUE_ABANDON":
                s.abandoned = True
            if e.queue_depth is not None:
                queue_depths.append((ets, e.queue_depth))
        elif e.event_type == "ZONE_ENTER" and e.zone_id and e.zone_id != BILLING_ZONE:
            s.zones_visited.add(e.zone_id)
        if e.event_type in ("ZONE_EXIT", "ZONE_DWELL") and e.zone_id:
            s.zone_dwell_ms[e.zone_id] = max(s.zone_dwell_ms.get(e.zone_id, 0), e.dwell_ms)

    # conversion: billing presence within conv_window BEFORE a txn
    for s in sessions.values():
        for bt in s.billing_times:
            if any(bt <= tt <= bt + conv_window for tt in txn_times):
                s.converted = True
                break

    purchases = sum(1 for t in txn_times if t < end)  # tail beyond `end` is only for conversion
    return WindowData(sessions=sessions, purchases=purchases, queue_depths=queue_depths)

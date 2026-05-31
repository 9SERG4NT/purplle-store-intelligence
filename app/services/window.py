"""Time-window resolution.

"Today" is anchored to the freshest data: the store-local calendar day that
contains the most recent event for the store. This makes metrics behave like
real-time both for historical replays and a genuine live feed (the brief's
"real-time, not cached from yesterday"). An explicit ?date=YYYY-MM-DD overrides.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Event
from app.services.store_layout import store_timezone


def latest_event_ts(db: Session, store_id: str) -> datetime | None:
    ts = db.execute(
        select(func.max(Event.ts)).where(Event.store_id == store_id)
    ).scalar_one_or_none()
    return _as_utc(ts)


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _day_bounds(moment: datetime, tzname: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tzname)
    local = moment.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def resolve_window(
    db: Session, store_id: str, date_str: str | None = None
) -> tuple[datetime | None, datetime | None]:
    """Return (start, end) UTC for the metrics window, or (None, None) if no data."""
    tzname = store_timezone(store_id)
    if date_str:
        day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo(tzname))
        return _day_bounds(day, tzname)
    latest = latest_event_ts(db, store_id)
    if latest is None:
        return None, None
    return _day_bounds(latest, tzname)

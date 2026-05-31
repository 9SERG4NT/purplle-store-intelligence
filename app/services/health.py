"""/health — the first thing an on-call engineer checks.

Staleness is measured against *ingestion* recency (is the feed still flowing?),
not event timestamps, so a stopped pipeline shows STALE_FEED even when replaying
historical footage.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.models import Event
from app.schemas import HealthResponse, StoreHealth
from app.services.store_layout import known_store_ids


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def compute_health(db: DbSession) -> HealthResponse:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    db_connected = True
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db_connected = False

    store_rows: list[StoreHealth] = []
    if db_connected:
        # union of configured stores and any store that has actually sent events
        store_ids = set(known_store_ids())
        store_ids.update(db.execute(select(Event.store_id).distinct()).scalars().all())
        for sid in sorted(store_ids):
            row = db.execute(
                select(func.max(Event.ts), func.max(Event.ingested_at), func.count())
                .where(Event.store_id == sid)
            ).one()
            last_event, last_ingest, count = _as_utc(row[0]), _as_utc(row[1]), row[2]
            lag = (now - last_ingest).total_seconds() if last_ingest else None
            stale = bool(lag is not None and lag > settings.stale_feed_minutes * 60)
            store_rows.append(StoreHealth(
                store_id=sid, last_event_ts=last_event, last_ingest_ts=last_ingest,
                lag_seconds=round(lag, 1) if lag is not None else None,
                stale_feed=stale, event_count=count,
            ))

    return HealthResponse(
        status="ok" if db_connected else "degraded",
        version=settings.app_version, db_connected=db_connected,
        server_time=now, stores=store_rows,
    )

"""Event ingestion: normalize, validate, dedup, store. Idempotent + partial success.

Accepts BOTH the official multi-source schema (entry/exit/zone_*/queue_*) and our
own emitted schema via app/services/normalize.py.

* Idempotent: real ids (event_id / queue_event_id) or a deterministic synthesised
  id are used as the primary key, so re-sending the same payload inserts nothing.
* Partial success: one malformed event does not reject the batch; valid events are
  stored and per-event errors are returned.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Event
from app.schemas import IngestResponse, RejectedEvent
from app.services.normalize import NormalizeError, normalize_event

MAX_BATCH = 500


def _int_or_none(v):
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def ingest_events(db: DbSession, raw_events: list[dict]) -> IngestResponse:
    received = len(raw_events)
    rejected: list[RejectedEvent] = []
    valid: dict[str, dict] = {}     # canonical events keyed by event_id (intra-batch dedup)
    valid_seen = 0

    for i, raw in enumerate(raw_events):
        try:
            ev = normalize_event(raw)
        except NormalizeError as exc:
            rejected.append(RejectedEvent(
                index=i, event_id=(raw or {}).get("event_id") if isinstance(raw, dict) else None,
                error=str(exc)))
            continue
        valid_seen += 1
        valid[ev["event_id"]] = ev

    intra_batch_dupes = valid_seen - len(valid)
    db_dupes = 0
    accepted = 0
    if valid:
        existing = set(db.execute(
            select(Event.event_id).where(Event.event_id.in_(list(valid.keys())))
        ).scalars().all())
        to_insert = []
        for eid, ev in valid.items():
            if eid in existing:
                db_dupes += 1
                continue
            to_insert.append(Event(
                event_id=ev["event_id"], store_id=ev["store_id"], camera_id=ev["camera_id"],
                visitor_id=ev["visitor_id"], event_type=ev["event_type"], ts=ev["ts"],
                zone_id=ev["zone_id"], dwell_ms=ev["dwell_ms"], is_staff=ev["is_staff"],
                confidence=ev["confidence"], queue_depth=ev["queue_depth"],
                sku_zone=ev["sku_zone"], session_seq=ev["session_seq"],
                zone_name=ev["zone_name"], zone_type=ev["zone_type"],
                is_revenue_zone=ev["is_revenue_zone"], gender=ev["gender"],
                age=_int_or_none(ev["age"]), age_bucket=ev["age_bucket"],
                group_id=ev["group_id"], group_size=_int_or_none(ev["group_size"]),
                wait_seconds=_int_or_none(ev["wait_seconds"]), abandoned=ev["abandoned"],
            ))
        if to_insert:
            db.add_all(to_insert)
            db.flush()
            accepted = len(to_insert)

    return IngestResponse(
        received=received, accepted=accepted,
        duplicates=intra_batch_dupes + db_dupes,
        rejected=len(rejected), rejected_details=rejected,
    )

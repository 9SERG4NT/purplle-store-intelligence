"""Event ingestion: validate, dedup, store. Idempotent + partial success.

* Idempotent by event_id: re-sending the same payload inserts nothing new and
  returns the same shape (duplicates counted, not errored).
* Partial success: one malformed event does not reject the batch; valid events
  are stored and per-event errors are returned.
"""
from __future__ import annotations

from datetime import timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Event
from app.schemas import EventIn, IngestResponse, RejectedEvent

MAX_BATCH = 500


def ingest_events(db: DbSession, raw_events: list[dict]) -> IngestResponse:
    received = len(raw_events)
    rejected: list[RejectedEvent] = []
    valid: dict[str, EventIn] = {}  # keyed by event_id -> de-dups within the batch
    valid_seen = 0                  # successfully-validated count (before intra-batch dedup)

    for i, raw in enumerate(raw_events):
        try:
            ev = EventIn.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first.get("loc", ()))
            rejected.append(RejectedEvent(
                index=i, event_id=(raw or {}).get("event_id"),
                error=f"{loc}: {first.get('msg', 'invalid')}",
            ))
            continue
        valid_seen += 1
        valid[ev.event_id] = ev  # last write wins for intra-batch dupes

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
                event_id=ev.event_id, store_id=ev.store_id, camera_id=ev.camera_id,
                visitor_id=ev.visitor_id, event_type=ev.event_type,
                ts=ev.timestamp.astimezone(timezone.utc),
                zone_id=ev.zone_id, dwell_ms=ev.dwell_ms, is_staff=ev.is_staff,
                confidence=ev.confidence, queue_depth=ev.metadata.queue_depth,
                sku_zone=ev.metadata.sku_zone, session_seq=ev.metadata.session_seq,
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

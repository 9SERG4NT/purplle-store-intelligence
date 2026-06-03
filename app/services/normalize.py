"""Normalise heterogeneous ingest events into one internal representation.

The provided sample_events.jsonl is a realistic *multi-source* stream — three
event families with different field names:

  * entry / exit         -> id_token, store_code, event_timestamp, gender_pred,
                            age_pred, group_id, group_size, is_face_hidden
  * zone_entered/_exited -> track_id, store_id (ST####), event_time, zone_name,
                            zone_type, is_revenue_zone, zone_hotspot_x/y, gender, age
  * queue_completed/
    queue_abandoned      -> queue_event_id, queue_join_ts, wait_seconds,
                            queue_position_at_join, abandoned

This module maps all of them (plus our own emitted schema) onto a single
canonical event dict the rest of the API understands, so the held-out scoring
set ingests cleanly regardless of which family it uses.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

# official (lowercase) + our (UPPER) event types -> canonical UPPER type
_TYPE_MAP = {
    "entry": "ENTRY", "exit": "EXIT",
    "zone_entered": "ZONE_ENTER", "zone_exited": "ZONE_EXIT",
    "queue_completed": "QUEUE_COMPLETED", "queue_abandoned": "BILLING_QUEUE_ABANDON",
    "zone_dwell": "ZONE_DWELL", "reentry": "REENTRY",
    "billing_queue_join": "BILLING_QUEUE_JOIN", "billing_queue_abandon": "BILLING_QUEUE_ABANDON",
}
CANONICAL_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "QUEUE_COMPLETED", "REENTRY",
}
BILLING_ZONE_TYPES = {"BILLING", "QUEUE", "CHECKOUT"}


def canon_store(raw: str) -> str:
    """Unify the two official store-id formats (store_1076 == ST1076) without
    mangling ids that don't match that pattern (e.g. STORE_BLR_002 stays)."""
    if not raw:
        return raw
    m = re.fullmatch(r"(?:store_|st)(\d+)", raw.strip(), re.IGNORECASE)
    return f"ST{m.group(1)}" if m else raw


def _first(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return default


def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"yes", "true", "1", "y"}


def _synth_id(store, visitor, etype, ts, zone) -> str:
    raw = f"{store}|{visitor}|{etype}|{ts}|{zone}"
    return "ev_" + hashlib.sha1(raw.encode()).hexdigest()[:24]


class NormalizeError(ValueError):
    pass


def normalize_event(raw: dict) -> dict:
    """Return a canonical event dict, or raise NormalizeError for malformed input."""
    if not isinstance(raw, dict):
        raise NormalizeError("event is not an object")

    raw_type = str(_first(raw, "event_type", default="")).strip()
    etype = _TYPE_MAP.get(raw_type.lower(), raw_type.upper())
    if etype not in CANONICAL_TYPES:
        raise NormalizeError(f"unknown event_type {raw_type!r}")

    store_id = canon_store(str(_first(raw, "store_code", "store_id", default="")))
    if not store_id:
        raise NormalizeError("missing store_code/store_id")

    # visitor identity: id_token (entry/exit) | track_id (zone/queue) | our visitor_id
    visitor = _first(raw, "visitor_id", "id_token")
    if visitor is None and raw.get("track_id") is not None:
        visitor = f"T{raw['track_id']}"
    if visitor is None:
        raise NormalizeError("missing visitor identity (id_token/track_id/visitor_id)")
    visitor = str(visitor)

    ts = parse_ts(_first(raw, "timestamp", "event_timestamp", "event_time", "queue_join_ts"))
    if ts is None:
        raise NormalizeError("missing/invalid timestamp")

    meta = raw.get("metadata") or {}
    zone_id = _first(raw, "zone_id")
    queue_depth = _first(raw, "queue_position_at_join", default=meta.get("queue_depth"))
    if queue_depth is not None:
        try:
            queue_depth = int(queue_depth)
        except (TypeError, ValueError):
            queue_depth = None

    event_id = _first(raw, "event_id", "queue_event_id") or _synth_id(
        store_id, visitor, etype, ts.isoformat(), zone_id)

    zone_type = (_first(raw, "zone_type") or "")
    if zone_type:
        zone_type = str(zone_type).upper()
    # QUEUE_COMPLETED implies billing even if zone_type missing
    if etype in ("QUEUE_COMPLETED", "BILLING_QUEUE_ABANDON", "BILLING_QUEUE_JOIN") and not zone_type:
        zone_type = "BILLING"

    return {
        "event_id": str(event_id),
        "store_id": store_id,
        "camera_id": str(_first(raw, "camera_id", default="")),
        "visitor_id": visitor,
        "event_type": etype,
        "ts": ts,
        "zone_id": str(zone_id) if zone_id is not None else None,
        "zone_name": _first(raw, "zone_name"),
        "zone_type": zone_type or None,
        "is_revenue_zone": _to_bool(raw["is_revenue_zone"]) if "is_revenue_zone" in raw else None,
        "dwell_ms": int(_first(raw, "dwell_ms", default=meta.get("dwell_ms") or 0) or 0),
        "is_staff": _to_bool(raw.get("is_staff", False)),
        "confidence": float(_first(raw, "confidence", default=1.0)),
        "queue_depth": queue_depth,
        "sku_zone": _first(raw, "sku_zone", default=meta.get("sku_zone")),
        "session_seq": int(_first(raw, "session_seq", default=meta.get("session_seq") or 0) or 0),
        # demographics / grouping (official extras)
        "gender": _first(raw, "gender", "gender_pred"),
        "age": _first(raw, "age", "age_pred"),
        "age_bucket": _first(raw, "age_bucket"),
        "group_id": _first(raw, "group_id"),
        "group_size": _first(raw, "group_size"),
        "wait_seconds": _first(raw, "wait_seconds"),
        "abandoned": _to_bool(raw["abandoned"]) if "abandoned" in raw else None,
    }

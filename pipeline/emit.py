"""Event schema construction + JSONL emission.

This is the single source of truth for the on-the-wire event shape. The API's
Pydantic model (app/schemas.py) mirrors it; sample_events.jsonl is generated
from it so the contract can't drift.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TextIO

EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
}


def new_event_id() -> str:
    return str(uuid.uuid4())


def iso_utc(dt: datetime) -> str:
    """ISO-8601 in UTC with a trailing Z, e.g. 2026-04-10T14:50:03Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_event(
    *,
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    session_seq: int,
    zone_id: Optional[str] = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 1.0,
    queue_depth: Optional[int] = None,
    sku_zone: Optional[str] = None,
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event_type {event_type!r}")
    return {
        "event_id": new_event_id(),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": iso_utc(timestamp),
        "zone_id": zone_id,
        "dwell_ms": int(dwell_ms),
        "is_staff": bool(is_staff),
        "confidence": round(float(confidence), 4),
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": int(session_seq),
        },
    }


@dataclass
class EventWriter:
    """Writes events to JSONL, optionally also streaming them to the API."""

    path: Path
    _fh: TextIO | None = field(default=None, init=False)
    count: int = field(default=0, init=False)

    def __enter__(self) -> "EventWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        return self

    def write(self, event: dict[str, Any]) -> None:
        assert self._fh is not None
        self._fh.write(json.dumps(event, separators=(",", ":")) + "\n")
        self.count += 1

    def __exit__(self, *exc) -> None:
        if self._fh:
            self._fh.close()

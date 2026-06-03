"""Demo-only live replay: reset a store's events, then re-stream the bundled
sample over ~20s in a background thread so the dashboard visibly fills from zero.

This exists purely to demonstrate the real-time path from the UI (Part E). It is
clearly namespaced under /demo and never touched by the analytics endpoints.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import Event
from app.services.ingestion import ingest_events
from app.services.normalize import canon_store, parse_ts

_STATE = {"running": False, "store": None, "sent": 0, "total": 0}
_LOCK = threading.Lock()


def status() -> dict:
    return dict(_STATE)


def _sample_path() -> Path:
    p = Path(get_settings().store_layout_path).parent / "sample_events.jsonl"
    return p


def _ts_of(e: dict):
    return parse_ts(e.get("event_timestamp") or e.get("event_time")
                    or e.get("queue_join_ts") or e.get("timestamp"))


def _run(store_id: str, duration_s: float, steps: int) -> None:
    try:
        path = _sample_path()
        if not path.exists():
            return
        evs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        # keep only events that belong to the requested store (after id canonicalisation)
        evs = [e for e in evs
               if canon_store(str(e.get("store_code") or e.get("store_id") or "")) == store_id]
        evs.sort(key=lambda e: (_ts_of(e) or 0))
        _STATE.update(total=len(evs), sent=0)
        if not evs:
            return
        # wipe this store's events so the dashboard starts from zero,
        # then restore the prior-day baseline (keeps the conversion-drop anomaly meaningful)
        with SessionLocal() as db:
            db.query(Event).filter(Event.store_id == store_id).delete()
            db.commit()
        baseline = path.parent / "baseline_events.jsonl"
        if baseline.exists():
            rows = [json.loads(l) for l in baseline.read_text(encoding="utf-8").splitlines() if l.strip()]
            with SessionLocal() as db:
                ingest_events(db, rows)
                db.commit()
        per = max(1, len(evs) // max(1, steps))
        for i in range(0, len(evs), per):
            chunk = evs[i:i + per]
            with SessionLocal() as db:
                ingest_events(db, chunk)
                db.commit()
            _STATE["sent"] = min(len(evs), i + per)
            time.sleep(duration_s / steps)
    finally:
        _STATE["running"] = False


def start_replay(store_id: str, duration_s: float = 20.0, steps: int = 25) -> dict:
    with _LOCK:
        if _STATE["running"]:
            return {"status": "already_running", **status()}
        _STATE.update(running=True, store=store_id, sent=0, total=0)
    threading.Thread(target=_run, args=(store_id, duration_s, steps), daemon=True).start()
    return {"status": "started", "store": store_id}

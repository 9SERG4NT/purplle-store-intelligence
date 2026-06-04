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

_STATE = {"running": False, "store": None, "sent": 0, "total": 0, "gen": 0}
_LOCK = threading.Lock()


def status() -> dict:
    return dict(_STATE)


def _sample_paths() -> list[Path]:
    """Every bundled sample file, so a replay can target either store.
    sample_events.jsonl = store 1; events_store2_official.jsonl = store 2."""
    data = Path(get_settings().store_layout_path).parent
    return [p for p in (data / "sample_events.jsonl",
                        data / "events_store2_official.jsonl") if p.exists()]


def _ts_of(e: dict):
    return parse_ts(e.get("event_timestamp") or e.get("event_time")
                    or e.get("queue_join_ts") or e.get("timestamp"))


def _superseded(gen: int) -> bool:
    """True once a newer replay (e.g. a store switch) has started — so this one bows out."""
    return _STATE["gen"] != gen


def _run(store_id: str, duration_s: float, gen: int) -> None:
    try:
        paths = _sample_paths()
        if not paths:
            return
        evs: list[dict] = []
        for path in paths:
            evs += [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        # keep only events that belong to the requested store (after id canonicalisation)
        evs = [e for e in evs
               if canon_store(str(e.get("store_code") or e.get("store_id") or "")) == store_id]
        evs.sort(key=lambda e: (_ts_of(e) or 0))
        if _superseded(gen):
            return
        _STATE.update(total=len(evs), sent=0)
        if not evs:
            return
        # wipe this store's events so the dashboard starts from zero,
        # then restore the prior-day baseline (keeps the conversion-drop anomaly meaningful)
        with SessionLocal() as db:
            db.query(Event).filter(Event.store_id == store_id).delete()
            db.commit()
        baseline = paths[0].parent / "baseline_events.jsonl"
        if baseline.exists():
            rows = [json.loads(l) for l in baseline.read_text(encoding="utf-8").splitlines() if l.strip()]
            with SessionLocal() as db:
                ingest_events(db, rows)
                db.commit()
        # Spread events evenly across duration_s (≈30 visible updates) so a small
        # store and a large one both animate over the same wall-clock window,
        # instead of a 29-event store bursting through in a couple of seconds.
        n = len(evs)
        steps = min(n, 30)
        per = -(-n // steps)                 # ceil(n / steps)
        interval = duration_s / steps
        for i in range(0, n, per):
            if _superseded(gen):
                return
            with SessionLocal() as db:
                ingest_events(db, evs[i:i + per])
                db.commit()
            _STATE["sent"] = min(n, i + per)
            time.sleep(interval)
    finally:
        if not _superseded(gen):
            _STATE["running"] = False


def start_replay(store_id: str, duration_s: float = 18.0) -> dict:
    """Start (or preempt any in-flight) replay for store_id. A new call always wins,
    so switching stores in the dashboard immediately restarts the animation."""
    with _LOCK:
        _STATE["gen"] += 1
        gen = _STATE["gen"]
        _STATE.update(running=True, store=store_id, sent=0, total=0)
    threading.Thread(target=_run, args=(store_id, duration_s, gen), daemon=True).start()
    return {"status": "started", "store": store_id}

"""Replay events into the API in (simulated) real time — drives the dashboard.

Reads a JSONL event file, sorts by timestamp, and POSTs to /events/ingest while
preserving the original inter-event timing, compressed by --speed. This proves
the pipeline and API are genuinely connected (Part E), not batch-loaded.

    python scripts/replay.py --api http://localhost:8000 --speed 30
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent.parent / "data"


def _default_events() -> Path:
    real = DATA / "events.jsonl"           # produced by the real pipeline
    return real if real.exists() else DATA / "sample_events.jsonl"  # synthetic demo


def _ts(e: dict) -> datetime:
    """Robust to both schemas: PDF (timestamp, ...Z) and official
    (event_timestamp / event_time / queue_join_ts, microseconds, no Z)."""
    raw = (e.get("timestamp") or e.get("event_timestamp") or e.get("event_time")
           or e.get("queue_join_ts") or "1970-01-01T00:00:00")
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace("+00:00", ""))


def replay(api: str, events_paths: list[Path], speed: float, batch: int) -> None:
    # ingest prior-day baseline first (instant, no delay) so /anomalies has history
    baseline = events_paths[0].parent / "baseline_events.jsonl"
    if baseline.exists():
        rows = [json.loads(l) for l in baseline.read_text(encoding="utf-8").splitlines() if l.strip()]
        for i in range(0, len(rows), 200):
            httpx.post(f"{api}/events/ingest", json={"events": rows[i:i + 200]}, timeout=30.0)
        print(f"seeded {len(rows)} baseline (prior-day) events")

    # merge one or more event files (e.g. store 1 + store 2) into a single time-ordered stream
    events: list[dict] = []
    for p in events_paths:
        events += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    events.sort(key=_ts)
    if not events:
        print("no events to replay")
        return
    names = ", ".join(p.name for p in events_paths)
    print(f"replaying {len(events)} events from {names} at {speed}x -> {api}")

    t0 = _ts(events[0])
    wall0 = time.perf_counter()
    buffer: list[dict] = []
    sent = 0

    def flush():
        nonlocal sent
        if not buffer:
            return
        r = httpx.post(f"{api}/events/ingest", json={"events": buffer}, timeout=30.0)
        r.raise_for_status()
        body = r.json()
        sent += body["accepted"]
        print(f"  +{body['accepted']} accepted ({body['duplicates']} dup, {body['rejected']} rej) "
              f"| total {sent}")
        buffer.clear()

    for e in events:
        target = (_ts(e) - t0).total_seconds() / max(speed, 1e-6)
        now = time.perf_counter() - wall0
        if target > now:
            flush()                      # send what we have, then wait
            time.sleep(target - now)
        buffer.append(e)
        if len(buffer) >= batch:
            flush()
    flush()
    print(f"done: {sent} events ingested")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--events", default=None,
                    help="JSONL event file(s); comma-separate several (e.g. store 1 + store 2)")
    ap.add_argument("--speed", type=float, default=30.0, help="time-compression factor")
    ap.add_argument("--batch", type=int, default=25)
    args = ap.parse_args()
    if args.events:
        paths = [Path(p.strip()) for p in args.events.split(",") if p.strip()]
    else:
        paths = [_default_events()]
    replay(args.api, paths, args.speed, args.batch)


if __name__ == "__main__":
    main()

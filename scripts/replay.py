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
    return datetime.strptime(e["timestamp"], "%Y-%m-%dT%H:%M:%SZ")


def replay(api: str, events_path: Path, speed: float, batch: int) -> None:
    events = [json.loads(l) for l in events_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    events.sort(key=_ts)
    if not events:
        print("no events to replay")
        return
    print(f"replaying {len(events)} events from {events_path.name} at {speed}x -> {api}")

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
    ap.add_argument("--events", default=None)
    ap.add_argument("--speed", type=float, default=30.0, help="time-compression factor")
    ap.add_argument("--batch", type=int, default=25)
    args = ap.parse_args()
    path = Path(args.events) if args.events else _default_events()
    replay(args.api, path, args.speed, args.batch)


if __name__ == "__main__":
    main()

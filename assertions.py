"""Example acceptance assertions the Intelligence API must pass.

Self-contained: it ingests a known batch under a fresh per-run store id (so it is
deterministic regardless of any other data the server already holds) and asserts
correctness of ingest, metrics, funnel, heatmap, health and error handling.

    docker compose up -d
    python assertions.py --api http://localhost:8000
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

BASE = datetime(2026, 4, 10, 14, 50, 0, tzinfo=timezone.utc)
RUN = uuid.uuid4().hex[:6]
STORE = f"STORE_ASSERT_{RUN}"          # fresh, isolated store per run
_n = 0


def ev(vid, etype, off, **kw):
    global _n
    _n += 1
    t = BASE + timedelta(seconds=off)
    return {
        "event_id": f"{RUN}-{_n}", "store_id": STORE, "camera_id": kw.get("cam", "CAM_ENTRY_01"),
        "visitor_id": vid, "event_type": etype, "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": kw.get("zone"), "dwell_ms": kw.get("dwell", 0), "is_staff": kw.get("staff", False),
        "confidence": kw.get("conf", 0.9),
        "metadata": {"queue_depth": kw.get("qd"), "sku_zone": kw.get("zone"), "session_seq": kw.get("seq", 1)},
    }


BATCH = [
    ev("VIS_a", "ENTRY", 0),
    ev("VIS_a", "ZONE_ENTER", 30, zone="SKINCARE", cam="CAM_FLOOR_01", seq=2),
    ev("VIS_a", "ZONE_EXIT", 90, zone="SKINCARE", dwell=60000, cam="CAM_FLOOR_01", seq=3),
    ev("VIS_a", "BILLING_QUEUE_JOIN", 120, zone="BILLING", qd=3, cam="CAM_BILLING_01", seq=4),
    ev("VIS_b", "ENTRY", 5),
    ev("VIS_c", "ENTRY", 8),
    ev("VIS_c", "EXIT", 40, seq=2),
    ev("VIS_c", "REENTRY", 130, seq=3),                 # same visitor -> not a new entrant
    ev("VIS_s", "ZONE_ENTER", 10, zone="BILLING", staff=True, cam="CAM_BILLING_01"),  # staff
    ev("VIS_lowconf", "ENTRY", 12, conf=0.05),          # low confidence -> must persist
]

PASSED = FAILED = 0


def check(name: str, ok: bool):
    global PASSED, FAILED
    print(f"{'PASS' if ok else 'FAIL'} · {name}")
    PASSED += ok
    FAILED += (not ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    api = ap.parse_args().api
    c = httpx.Client(base_url=api, timeout=30.0)

    # 1. ingest accepts the batch with no 5xx
    r = c.post("/events/ingest", json={"events": BATCH})
    check("1. POST /events/ingest returns 200", r.status_code == 200)
    body = r.json()
    check("2. all events accepted (incl. low-confidence)", body["accepted"] == len(BATCH))

    # 3. idempotency: re-sending is a no-op
    r2 = c.post("/events/ingest", json={"events": BATCH}).json()
    check("3. re-ingest is idempotent", r2["accepted"] == 0 and r2["duplicates"] == len(BATCH))

    # 4-7. metrics
    m = c.get(f"/stores/{STORE}/metrics")
    check("4. GET metrics returns valid JSON", m.status_code == 200 and "conversion_rate" in m.json())
    m = m.json()
    # 4 distinct non-staff visitors: a, b, c (re-entry = same id), and the
    # low-confidence entrant (retained, not suppressed). Staff VIS_s excluded.
    check("5. unique visitors excludes staff (==4)", m["unique_visitors"] == 4)
    check("6. one staff member excluded", m["staff_excluded"] == 1)
    check("7. current queue depth == 3", m["current_queue_depth"] == 3)

    # 8. funnel is monotonic and session-based (re-entry counted once)
    f = c.get(f"/stores/{STORE}/funnel").json()
    series = [s["count"] for s in f["stages"]]
    check("8. funnel monotonic non-increasing", series == sorted(series, reverse=True))
    entered = next(s["count"] for s in f["stages"] if s["stage"] == "entered")
    check("9. funnel entered==4 (re-entry not double-counted)", entered == 4)

    # 10. heatmap scores normalised 0..100
    h = c.get(f"/stores/{STORE}/heatmap").json()
    check("10. heatmap scores in [0,100]", all(0 <= cell["score"] <= 100 for cell in h["cells"]))

    # bonus operational checks
    health = c.get("/health").json()
    check("11. /health db_connected", health["db_connected"] is True)
    check("12. unknown store -> 404", c.get("/stores/NOPE_XYZ/metrics").status_code == 404)
    big = [ev(f"V{i}", "ENTRY", i) for i in range(501)]
    check("13. batch > 500 -> 413", c.post("/events/ingest", json={"events": big}).status_code == 413)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

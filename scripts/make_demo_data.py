"""Generate a SMALL SYNTHETIC dataset so the system is demonstrable on a clean
clone WITHOUT committing any real footage-derived data (challenge rule).

Outputs:
  data/sample_events.jsonl   ~200 events, valid against the schema (deliverable)
  data/demo_pos.csv          a handful of POS rows aligned so some visits convert

These are HAND-SYNTHESISED (clearly labelled), not produced from the CCTV clips.
The real pipeline writes the real data/events.jsonl + data/pos_transactions.csv
(both git-ignored). The API computes identically over either.
"""
from __future__ import annotations

import csv
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(2026)

STORE = "STORE_BLR_002"
BASE = datetime(2026, 4, 10, 14, 50, 0, tzinfo=timezone.utc)  # 20:20 IST
FLOOR_ZONES = [("SKINCARE", "skin", "CAM_FLOOR_01"),
               ("MAKEUP", "makeup", "CAM_FLOOR_02"),
               ("NAIL_FRAGRANCE", "personal-care", "CAM_FLOOR_02"),
               ("MAKEUP_STUDIO", "makeup", "CAM_FLOOR_01")]
DATA = Path(__file__).resolve().parent.parent / "data"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ev(events, vid, etype, t, *, cam, zone=None, dwell=0, staff=False, conf=0.9, qd=None):
    seq = sum(1 for e in events if e["visitor_id"] == vid) + 1
    events.append({
        "event_id": str(uuid.uuid4()), "store_id": STORE, "camera_id": cam,
        "visitor_id": vid, "event_type": etype, "timestamp": iso(t), "zone_id": zone,
        "dwell_ms": dwell, "is_staff": staff, "confidence": round(conf, 3),
        "metadata": {"queue_depth": qd, "sku_zone": zone, "session_seq": seq},
    })


def build():
    events: list[dict] = []
    pos: list[dict] = []
    converters_billing: list[datetime] = []
    t = BASE

    # 2 staff members moving through zones (must be EXCLUDED from metrics)
    for i, (cam, zone) in enumerate([("CAM_BACK_01", "BACKROOM"), ("CAM_BILLING_01", "BILLING")]):
        vid = f"VIS_staff{i}"
        st = BASE + timedelta(seconds=5 + i)
        ev(events, vid, "ZONE_ENTER", st, cam=cam, zone=zone, staff=True, conf=0.8)
        for k in range(1, 4):  # long presence -> dwell pings
            ev(events, vid, "ZONE_DWELL", st + timedelta(seconds=30 * k), cam=cam,
               zone=zone, dwell=30000 * k, staff=True, conf=0.8)
        ev(events, vid, "ZONE_EXIT", st + timedelta(seconds=120), cam=cam, zone=zone,
           dwell=120000, staff=True, conf=0.8)

    n_customers = 46
    for i in range(n_customers):
        vid = f"VIS_{uuid.uuid4().hex[:8]}"
        t = t + timedelta(seconds=random.randint(20, 90))  # spread over ~40 min
        conf = round(random.uniform(0.35, 0.95), 3)
        ev(events, vid, "ENTRY", t, cam="CAM_ENTRY_01", conf=conf)

        # ~65% browse a named zone
        browsed = random.random() < 0.65
        last = t
        if browsed:
            zone, dept, cam = random.choice(FLOOR_ZONES)
            zt = t + timedelta(seconds=random.randint(8, 25))
            dwell_s = random.randint(15, 95)
            ev(events, vid, "ZONE_ENTER", zt, cam=cam, zone=zone, conf=conf)
            for k in range(1, dwell_s // 30 + 1):  # ZONE_DWELL every 30s
                ev(events, vid, "ZONE_DWELL", zt + timedelta(seconds=30 * k), cam=cam,
                   zone=zone, dwell=30000 * k, conf=conf)
            ev(events, vid, "ZONE_EXIT", zt + timedelta(seconds=dwell_s), cam=cam,
               zone=zone, dwell=dwell_s * 1000, conf=conf)
            last = zt + timedelta(seconds=dwell_s)

        # of browsers, ~55% reach billing
        if browsed and random.random() < 0.55:
            qd = random.randint(0, 5)
            bt = last + timedelta(seconds=random.randint(5, 20))
            etype = "BILLING_QUEUE_JOIN" if qd > 0 else "ZONE_ENTER"
            ev(events, vid, etype, bt, cam="CAM_BILLING_01", zone="BILLING", qd=qd, conf=conf)
            purchase = random.random() < 0.6
            if purchase:
                converters_billing.append(bt)
                ev(events, vid, "ZONE_EXIT", bt + timedelta(seconds=random.randint(30, 80)),
                   cam="CAM_BILLING_01", zone="BILLING", dwell=random.randint(30, 80) * 1000, conf=conf)
            else:
                ev(events, vid, "BILLING_QUEUE_ABANDON",
                   bt + timedelta(seconds=random.randint(40, 120)),
                   cam="CAM_BILLING_01", zone="BILLING", qd=qd, conf=conf)

        # one explicit re-entry: exit then REENTRY with the same visitor_id
        if i == 3:
            xt = last + timedelta(seconds=30)
            ev(events, vid, "EXIT", xt, cam="CAM_ENTRY_01", conf=conf)
            ev(events, vid, "REENTRY", xt + timedelta(seconds=90), cam="CAM_ENTRY_01", conf=conf)

    # POS: align a txn shortly AFTER each converter's billing time (-> conversion)
    n = 0
    for bt in converters_billing:
        n += 1
        pos.append({"store_id": STORE, "transaction_id": f"TXN_demo_{n:03d}",
                    "timestamp": iso(bt + timedelta(seconds=random.randint(30, 180))),
                    "basket_value_inr": round(random.uniform(199, 3200), 2)})
    # a couple of extra (non-correlated) txns earlier in the day
    for j in range(2):
        n += 1
        pos.append({"store_id": STORE, "transaction_id": f"TXN_demo_{n:03d}",
                    "timestamp": iso(BASE - timedelta(hours=2, minutes=17 * j)),
                    "basket_value_inr": round(random.uniform(149, 1800), 2)})

    events.sort(key=lambda e: e["timestamp"])
    pos.sort(key=lambda r: r["timestamp"])
    return events, pos


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    events, pos = build()
    with (DATA / "sample_events.jsonl").open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")
    with (DATA / "demo_pos.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["store_id", "transaction_id", "timestamp", "basket_value_inr"])
        w.writeheader()
        w.writerows(pos)
    print(f"wrote {len(events)} synthetic events -> data/sample_events.jsonl")
    print(f"wrote {len(pos)} synthetic POS rows -> data/demo_pos.csv")


if __name__ == "__main__":
    main()

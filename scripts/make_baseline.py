"""Generate SYNTHETIC prior-day baseline events (official schema) so the
/anomalies CONVERSION_DROP check has a 7-day average to compare against.

The provided footage is a single day, so a trailing baseline doesn't exist in
the real data. These prior days are clearly synthetic (high, steady conversion)
and only feed the anomaly baseline — today's real conversion is then flagged as
a drop. Output: data/baseline_events.jsonl (committed).
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

random.seed(11)
STORE = "STORE_BLR_002"
DATA = Path(__file__).resolve().parent.parent / "data"
ZONES = [("Z_SKIN", "Skincare", "SHELF"), ("Z_MAKEUP", "Makeup", "SHELF"),
         ("Z_FRAG", "Fragrance", "UNIT")]


def iso(dt):
    return dt.replace(tzinfo=None).isoformat()


def day_events(day: datetime, n=18, conv=0.78):
    evs = []
    t = day.replace(hour=13, minute=0, second=0)
    for i in range(n):
        t = t + timedelta(seconds=random.randint(60, 240))
        tok = f"ID_{day.strftime('%m%d')}_{i:03d}"
        evs.append({"event_type": "entry", "id_token": tok, "store_code": STORE,
                    "camera_id": "cam1", "event_timestamp": iso(t), "is_staff": False,
                    "gender_pred": random.choice(["F", "M"]), "age_pred": random.randint(20, 45),
                    "age_bucket": "25-34", "is_face_hidden": True, "group_id": None, "group_size": None})
        z = random.choice(ZONES)
        zt = t + timedelta(seconds=20)
        for kind, tt in (("zone_entered", zt), ("zone_exited", zt + timedelta(seconds=random.randint(20, 90)))):
            evs.append({"event_type": kind, "id_token": tok, "track_id": 1000 + i, "store_id": STORE,
                        "camera_id": "CAM2", "zone_id": z[0], "zone_name": z[1], "zone_type": z[2],
                        "is_revenue_zone": "Yes", "event_time": iso(tt), "gender": None, "age": None, "age_bucket": None})
        # most convert (queue_completed), the rest abandon -> high baseline conversion
        bt = t + timedelta(seconds=150)
        served = random.random() < conv
        evs.append({"queue_event_id": str(uuid.uuid4()),
                    "event_type": "queue_completed" if served else "queue_abandoned",
                    "id_token": tok, "track_id": 1000 + i, "store_id": STORE, "camera_id": "CAM6",
                    "zone_id": "BILLING", "zone_name": "Billing Counter Queue", "zone_type": "BILLING",
                    "is_revenue_zone": "Yes", "queue_join_ts": iso(bt),
                    "queue_served_ts": iso(bt + timedelta(seconds=10)) if served else None,
                    "queue_exit_ts": iso(bt + timedelta(seconds=random.randint(15, 90))),
                    "wait_seconds": random.randint(5, 80), "queue_position_at_join": random.randint(0, 3),
                    "abandoned": not served, "gender": None, "age": None, "age_bucket": None})
    return evs


def main():
    # the real footage day is 2026-04-10; seed the 3 days before it
    base = datetime(2026, 4, 10)
    out = []
    for d in (3, 2, 1):
        out += day_events(base - timedelta(days=d))
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "baseline_events.jsonl").open("w", encoding="utf-8") as fh:
        for e in out:
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")
    print(f"wrote {len(out)} baseline events (3 prior days) -> data/baseline_events.jsonl")


if __name__ == "__main__":
    main()

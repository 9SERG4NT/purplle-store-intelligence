# PROMPT: "Write pytest tests for the cv2-free detection-logic modules of a retail
#   CCTV pipeline: geometry (point-in-polygon, entry-line crossing direction),
#   appearance histogram similarity, POS CSV normalisation, and the SessionManager
#   that turns per-camera tracklets into entry/exit/re-entry events. Cover the
#   challenge edge cases explicitly: a GROUP of 3 entering together must yield 3
#   ENTRY events (not 1); a customer leaving and returning must yield REENTRY with
#   the SAME visitor_id (not a 2nd ENTRY); staff (backroom / behind counter / dark
#   uniform) must be flagged is_staff; billing presence with no following POS txn
#   must yield BILLING_QUEUE_ABANDON."
# CHANGES MADE: Tightened the re-entry test to assert a single unique visitor_id
#   across ENTRY+EXIT+REENTRY (the AI initially only checked the event types).
#   Added orthogonal appearance descriptors so distinct people don't merge, and
#   added the convert-vs-abandon pair so POS correlation is covered both ways.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import associate
import geometry
import pos
from emit import EVENT_TYPES, build_event, iso_utc
from tracklets import Crossing, Tracklet, ZoneInterval, descriptor_similarity

T0 = datetime(2026, 4, 10, 14, 50, 0, tzinfo=timezone.utc)
# four orthogonal-ish clothing signatures so different people do not merge
SIG = {
    "a": [1.0, 0.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0, 0.0],
    "c": [0.0, 0.0, 1.0, 0.0], "d": [0.0, 0.0, 0.0, 1.0],
}


def _entry_tracklet(sig, t_off, direction, tid, dur=2.0):
    t = T0 + timedelta(seconds=t_off)
    return Tracklet(
        camera_id="CAM_ENTRY_01", role="entry", local_track_id=tid,
        t_start=t, t_end=t + timedelta(seconds=dur), n_frames=10, conf_mean=0.9,
        descriptor=SIG[sig], crossings=[Crossing(t=t + timedelta(seconds=0.5), direction=direction)],
    )


# ---- geometry ---------------------------------------------------------------

def test_point_in_polygon():
    square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert geometry.point_in_polygon((0.5, 0.5), square)
    assert not geometry.point_in_polygon((1.5, 0.5), square)


def test_crossing_direction_inbound_and_outbound():
    a, b = (0.5, 0.0), (0.5, 1.0)      # vertical line
    inside = (0.1, 0.5)                # inside is to the left
    assert geometry.crossing_direction(a, b, inside, (0.8, 0.5), (0.2, 0.5)) == "inbound"
    assert geometry.crossing_direction(a, b, inside, (0.2, 0.5), (0.8, 0.5)) == "outbound"
    assert geometry.crossing_direction(a, b, inside, (0.8, 0.5), (0.7, 0.5)) is None


def test_foot_point_is_bottom_centre():
    assert geometry.foot_point((100, 100, 300, 500), 1000, 1000) == (0.2, 0.5)


# ---- appearance similarity --------------------------------------------------

def test_descriptor_similarity():
    assert descriptor_similarity(SIG["a"], SIG["a"]) == 1.0
    assert descriptor_similarity(SIG["a"], SIG["b"]) < 0.55
    assert descriptor_similarity(None, SIG["a"]) == 0.0


# ---- POS normalisation ------------------------------------------------------

def test_pos_normalise_collapses_skus_to_orders(tmp_path):
    raw = tmp_path / "raw.csv"
    # official POS has order_id per LINE-ITEM, so we group by (store, date, time)
    raw.write_text(
        "order_id,order_date,order_time,store_id,total_amount\n"
        "1,10-04-2026,14:38:12,ST1008,1240.00\n"
        "2,10-04-2026,14:38:12,ST1008,680.00\n"        # same order (same timestamp), 2 SKUs
        "3,10-04-2026,14:41:55,ST1008,500.00\n",
        encoding="utf-8",
    )
    rows = pos.normalise(raw)
    assert len(rows) == 2                               # collapsed to 2 orders by timestamp
    o1 = next(r for r in rows if r["timestamp"] == "2026-04-10T09:08:12Z")  # 14:38 IST -> UTC
    assert o1["basket_value_inr"] == 1920.0            # 1240 + 680


# ---- SessionManager: counting + edge cases ----------------------------------

def test_group_of_three_yields_three_entries():
    mgr = associate.SessionManager(store_id="STORE_BLR_002")
    mgr.ingest([
        _entry_tracklet("a", 0, "inbound", 1),
        _entry_tracklet("b", 1, "inbound", 2),
        _entry_tracklet("c", 2, "inbound", 3),
    ])
    events = mgr.build_events()
    entries = [e for e in events if e["event_type"] == "ENTRY"]
    assert len(entries) == 3
    assert len({e["visitor_id"] for e in entries}) == 3


def test_reentry_reuses_visitor_id():
    mgr = associate.SessionManager(store_id="STORE_BLR_002")
    mgr.ingest([
        _entry_tracklet("a", 0, "inbound", 1),       # ENTRY
        _entry_tracklet("a", 28, "outbound", 2),     # EXIT (same appearance)
        _entry_tracklet("a", 118, "inbound", 3),     # REENTRY (same appearance)
    ])
    events = mgr.build_events()
    types = [e["event_type"] for e in events]
    assert "ENTRY" in types and "EXIT" in types and "REENTRY" in types
    assert types.count("ENTRY") == 1                  # NOT a second ENTRY
    assert len({e["visitor_id"] for e in events}) == 1  # one physical person


def test_staff_classification_signals():
    base = dict(camera_id="X", role="floor", local_track_id=9, t_start=T0,
                t_end=T0 + timedelta(seconds=10), n_frames=10, conf_mean=0.9)
    assert associate.classify_staff(Tracklet(in_backroom=True, **base))
    assert associate.classify_staff(Tracklet(behind_counter_frac=0.8, **base))
    assert associate.classify_staff(Tracklet(dark_fraction=0.6, clip_fraction=0.7, **base))
    assert not associate.classify_staff(Tracklet(dark_fraction=0.6, clip_fraction=0.1, **base))
    assert associate.classify_staff(Tracklet(vlm_is_staff=True, dark_fraction=0.0, **base))


def _billing_tracklet(t_off, dur, qd):
    t = T0 + timedelta(seconds=t_off)
    zi = ZoneInterval(zone_id="BILLING", department="billing", t_enter=t,
                      t_exit=t + timedelta(seconds=dur), camera_id="CAM_BILLING_01",
                      queue_depth_at_join=qd)
    return Tracklet(camera_id="CAM_BILLING_01", role="billing", local_track_id=5,
                    t_start=t, t_end=t + timedelta(seconds=dur), n_frames=20,
                    conf_mean=0.8, descriptor=SIG["d"], zone_intervals=[zi])


def test_billing_abandon_when_no_purchase_follows():
    mgr = associate.SessionManager(store_id="STORE_BLR_002", pos_txn_times=[])
    mgr.ingest([_billing_tracklet(0, 40, qd=3)])
    types = [e["event_type"] for e in mgr.build_events()]
    assert "BILLING_QUEUE_JOIN" in types
    assert "BILLING_QUEUE_ABANDON" in types


def test_billing_converts_when_purchase_follows():
    txn = T0 + timedelta(seconds=60)  # within 5 min after billing exit
    mgr = associate.SessionManager(store_id="STORE_BLR_002", pos_txn_times=[txn])
    mgr.ingest([_billing_tracklet(0, 40, qd=3)])
    types = [e["event_type"] for e in mgr.build_events()]
    assert "BILLING_QUEUE_ABANDON" not in types


# ---- emit schema ------------------------------------------------------------

def test_build_event_shape_and_iso():
    e = build_event(store_id="S", camera_id="C", visitor_id="V", event_type="ENTRY",
                    timestamp=T0, session_seq=1)
    assert set(e) == {"event_id", "store_id", "camera_id", "visitor_id", "event_type",
                      "timestamp", "zone_id", "dwell_ms", "is_staff", "confidence", "metadata"}
    assert e["timestamp"].endswith("Z")
    assert e["event_type"] in EVENT_TYPES
    assert iso_utc(T0) == "2026-04-10T14:50:00Z"

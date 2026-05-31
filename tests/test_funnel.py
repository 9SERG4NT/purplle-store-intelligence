# PROMPT: "Write pytest tests for GET /stores/{id}/funnel. The funnel is Entry ->
#   Zone Visit -> Billing Queue -> Purchase, the unit is a session (visitor_id),
#   it must be monotonically non-increasing, and re-entries must NOT double-count a
#   visitor. Verify drop-off percentages and that a visitor who re-enters (same
#   visitor_id, ENTRY then REENTRY) is counted once."
# CHANGES MADE: Added an explicit monotonicity assertion across all stages (the AI
#   only checked individual counts). Added a staff session to confirm staff are
#   excluded from the funnel too.
from __future__ import annotations

STORE = "STORE_BLR_002"


def test_funnel_is_session_based_and_monotonic(client, seed, add_pos, mk):
    seed([
        # a: full journey -> purchase
        mk("VIS_a", "ENTRY", offset_s=0),
        mk("VIS_a", "ZONE_ENTER", offset_s=20, zone="SKINCARE", camera_id="CAM_FLOOR_01", seq=2),
        mk("VIS_a", "BILLING_QUEUE_JOIN", offset_s=60, zone="BILLING", queue_depth=1,
           camera_id="CAM_BILLING_01", seq=3),
        # b: browses, no billing
        mk("VIS_b", "ENTRY", offset_s=5),
        mk("VIS_b", "ZONE_ENTER", offset_s=25, zone="MAKEUP", camera_id="CAM_FLOOR_02", seq=2),
        # c: enters only
        mk("VIS_c", "ENTRY", offset_s=8),
        # staff excluded
        mk("VIS_s", "ENTRY", offset_s=9, is_staff=True),
    ])
    add_pos([("TXN_1", "2026-04-10T14:51:30Z", 999.0)])  # converts a's billing visit
    f = client.get(f"/stores/{STORE}/funnel").json()
    counts = {s["stage"]: s["count"] for s in f["stages"]}
    assert counts["entered"] == 3                 # a, b, c (staff excluded)
    assert counts["browsed_zone"] == 2            # a, b
    assert counts["billing_queue"] == 1           # a
    assert counts["purchased"] == 1               # a
    series = [s["count"] for s in f["stages"]]
    assert series == sorted(series, reverse=True)  # monotonic non-increasing
    assert f["overall_conversion_pct"] == round(1 / 3 * 100, 1)


def test_reentry_not_double_counted(client, seed, mk):
    seed([
        mk("VIS_a", "ENTRY", offset_s=0),
        mk("VIS_a", "EXIT", offset_s=30, seq=2),
        mk("VIS_a", "REENTRY", offset_s=120, seq=3),  # same visitor_id
        mk("VIS_b", "ENTRY", offset_s=10),
    ])
    f = client.get(f"/stores/{STORE}/funnel").json()
    entered = next(s["count"] for s in f["stages"] if s["stage"] == "entered")
    assert entered == 2  # a (once, despite re-entry) + b

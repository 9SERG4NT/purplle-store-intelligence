# PROMPT: "Write pytest tests for GET /stores/{id}/anomalies. Detect: billing queue
#   spike (depth >= warn threshold -> WARN, >= critical -> CRITICAL), dead zone (a
#   named product zone with no customer visit in the last 30 min, anchored to the
#   most recent event), and conversion drop vs a trailing average. Each anomaly must
#   carry a severity and a suggested_action. Verify a freshly-visited zone is NOT
#   flagged dead and a calm queue raises no spike."
# CHANGES MADE: Anchored 'now' to the latest event in the assertions (the AI assumed
#   wall-clock now, which never works for historical footage). Verified every
#   returned anomaly has a non-empty suggested_action.
from __future__ import annotations

STORE = "STORE_BLR_002"


def _types(resp):
    return {a["type"] for a in resp["anomalies"]}


def test_queue_spike_critical(client, seed, mk):
    seed([mk("VIS_a", "BILLING_QUEUE_JOIN", offset_s=10, zone="BILLING",
             queue_depth=7, camera_id="CAM_BILLING_01")])
    a = client.get(f"/stores/{STORE}/anomalies").json()
    spike = next(x for x in a["anomalies"] if x["type"] == "BILLING_QUEUE_SPIKE")
    assert spike["severity"] == "CRITICAL"
    assert spike["suggested_action"]


def test_queue_calm_no_spike(client, seed, mk):
    seed([mk("VIS_a", "BILLING_QUEUE_JOIN", offset_s=10, zone="BILLING",
             queue_depth=2, camera_id="CAM_BILLING_01")])
    a = client.get(f"/stores/{STORE}/anomalies").json()
    assert "BILLING_QUEUE_SPIKE" not in _types(a)


def test_dead_zone_flags_unvisited_not_fresh(client, seed, mk):
    seed([
        mk("VIS_a", "ENTRY", offset_s=0),
        mk("VIS_a", "ZONE_ENTER", offset_s=2399, zone="SKINCARE", camera_id="CAM_FLOOR_01", seq=2),
        mk("VIS_a", "ENTRY", offset_s=2400, event_id="ref"),  # newest event -> "now"
    ])
    a = client.get(f"/stores/{STORE}/anomalies").json()
    dead = {x["zone_id"] for x in a["anomalies"] if x["type"] == "DEAD_ZONE"}
    assert "MAKEUP" in dead          # never visited
    assert "SKINCARE" not in dead    # visited 1s before "now"
    for x in a["anomalies"]:
        assert x["suggested_action"]


def test_anomalies_empty_store(client):
    a = client.get(f"/stores/{STORE}/anomalies").json()
    assert a["anomalies"] == []
    assert a["reference_time"] is None

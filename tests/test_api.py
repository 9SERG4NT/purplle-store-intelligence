# PROMPT: "Write pytest tests for the operational surface of a FastAPI service:
#   GET /health (status, db_connected, per-store last-event/last-ingest, stale-feed
#   flag), unknown store -> 404, the heatmap endpoint (data_confidence LOW when < 20
#   sessions, normalised 0-100 scores), and graceful degradation: when the database
#   dependency fails the API must return HTTP 503 with a structured body and no
#   stack trace."
# CHANGES MADE: Implemented the 503 test by overriding the get_db dependency to
#   raise DatabaseUnavailable and asserting the structured body + trace_id, then
#   restoring the override so it can't leak into other tests.
from __future__ import annotations

from app.core.database import DatabaseUnavailable, get_db
from app.main import app

STORE = "STORE_BLR_002"


def test_health_ok(client, seed, mk):
    seed([mk("VIS_a", "ENTRY")])
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["db_connected"] is True
    store = next(s for s in h["stores"] if s["store_id"] == STORE)
    assert store["event_count"] == 1
    assert store["stale_feed"] is False  # just ingested


def test_unknown_store_404(client):
    r = client.get("/stores/STORE_DOES_NOT_EXIST/metrics")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "store_not_found"


def test_heatmap_low_confidence(client, seed, mk):
    seed([
        mk("VIS_a", "ENTRY"),
        mk("VIS_a", "ZONE_ENTER", offset_s=20, zone="SKINCARE", camera_id="CAM_FLOOR_01", seq=2),
        mk("VIS_a", "ZONE_EXIT", offset_s=80, zone="SKINCARE", dwell_ms=60000, camera_id="CAM_FLOOR_01", seq=3),
    ])
    h = client.get(f"/stores/{STORE}/heatmap").json()
    assert h["data_confidence"] == "LOW"          # < 20 sessions
    skincare = next(c for c in h["cells"] if c["zone_id"] == "SKINCARE")
    assert skincare["visits"] == 1 and skincare["score"] == 100.0  # only/most-visited zone
    assert all(0 <= c["score"] <= 100 for c in h["cells"])


def test_root_info(client):
    body = client.get("/").json()
    assert body["service"] == "store-intelligence"


def test_graceful_degradation_503(client):
    def boom():
        raise DatabaseUnavailable("simulated outage")
    app.dependency_overrides[get_db] = boom
    try:
        r = client.get(f"/stores/{STORE}/metrics")
        assert r.status_code == 503
        body = r.json()
        assert body["error"] == "database_unavailable"
        assert "trace_id" in body
        assert "Traceback" not in r.text  # no stack trace leaked
    finally:
        app.dependency_overrides.pop(get_db, None)

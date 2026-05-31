# PROMPT: "Write pytest tests for a FastAPI POST /events/ingest endpoint that must
#   be: (1) idempotent by event_id — posting the same payload twice stores nothing
#   new; (2) partial-success — one malformed event in a batch does not reject the
#   whole batch, valid ones are stored and per-event errors returned; (3) capped at
#   500 events per batch (413 otherwise); (4) must NOT drop low-confidence events.
#   Also verify intra-batch duplicates collapse and that received == accepted +
#   duplicates + rejected always holds."
# CHANGES MADE: Added the received==accepted+duplicates+rejected invariant as an
#   explicit assertion in every case (the AI checked counts individually but never
#   the conservation law, which is the real contract). Added the low-confidence
#   retention test since the brief calls it out specifically.
from __future__ import annotations


def _conserved(body):
    assert body["received"] == body["accepted"] + body["duplicates"] + body["rejected"]


def test_ingest_basic(seed, mk):
    body = seed([mk("VIS_a", "ENTRY"), mk("VIS_a", "ZONE_ENTER", zone="SKINCARE", seq=2)])
    assert body["accepted"] == 2 and body["duplicates"] == 0 and body["rejected"] == 0
    _conserved(body)


def test_idempotent_resend(client, mk):
    batch = [mk("VIS_a", "ENTRY", event_id="fixed-1"),
             mk("VIS_a", "EXIT", event_id="fixed-2", offset_s=30)]
    first = client.post("/events/ingest", json={"events": batch}).json()
    second = client.post("/events/ingest", json={"events": batch}).json()
    assert first["accepted"] == 2
    assert second["accepted"] == 0 and second["duplicates"] == 2
    _conserved(second)


def test_partial_success_on_malformed(seed, mk):
    good = mk("VIS_a", "ENTRY")
    bad_type = mk("VIS_b", "ENTRY"); bad_type["event_type"] = "TELEPORT"
    bad_ts = mk("VIS_c", "ENTRY"); bad_ts["timestamp"] = "not-a-date"
    body = seed([good, bad_type, bad_ts])
    assert body["accepted"] == 1 and body["rejected"] == 2
    assert {d["index"] for d in body["rejected_details"]} == {1, 2}
    _conserved(body)


def test_intra_batch_duplicates_collapse(seed, mk):
    e = mk("VIS_a", "ENTRY", event_id="dup")
    body = seed([e, dict(e)])  # same event_id twice in one batch
    assert body["received"] == 2 and body["accepted"] == 1 and body["duplicates"] == 1
    _conserved(body)


def test_batch_too_large_returns_413(client, mk):
    events = [mk(f"VIS_{i}", "ENTRY", event_id=f"e{i}") for i in range(501)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 413
    assert r.json()["detail"]["error"] == "batch_too_large"


def test_low_confidence_events_not_dropped(seed, mk):
    body = seed([mk("VIS_a", "ENTRY", confidence=0.05)])  # very low conf, must persist
    assert body["accepted"] == 1

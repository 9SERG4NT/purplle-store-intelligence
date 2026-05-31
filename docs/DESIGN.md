# DESIGN — Apex Retail Store Intelligence

## 1. The problem, restated

Offline stores are an analytics blind spot. We start from raw CCTV and finish
with a queryable, real-time view of the one metric that matters — **offline
conversion rate** — plus the operational signals (funnel drop-off, dwell
heatmap, queue/dead-zone anomalies, feed health) that make it actionable.

## 2. The dataset we actually got (and how we adapted)

The brief describes an idealised dataset (5 stores × 3 cameras × 20 min, a clean
`pos_transactions.csv`, `store_layout.json`). What was provided is different, and
the system is built around the **real** data:

- **One store** — Purplle, Brigade Road, Bangalore (`ST1008`). We expose it as
  `STORE_BLR_002` so the acceptance-gate URL works, and map the internal POS id.
- **Five cameras, ~2.5-min clips each.** Roles inferred from the footage:
  `CAM 3` = entry, `CAM 1` / `CAM 2` = main floor, `CAM 5` = billing, `CAM 4` =
  back-of-house stock (staff only).
- **POS is a real 40-column Purplle export** (line-level per SKU). `pipeline/pos.py`
  collapses it to the brief's clean schema (order-level, IST→UTC, no PII).
- We **authored `store_layout.json`** ourselves — normalised (resolution-
  independent) zone polygons + an entry counting line, hand-calibrated from sample
  frames (`scripts/calibrate_zones.py` overlays them to verify).
- The clips are short excerpts; the DVR clock (~20:11) isn't authoritative, so we
  anchor the event window to **20:20 IST** in the layout, which aligns billing-zone
  presence with the real **20:25 POS transaction** so conversion is meaningful.
  This is a deliberate, documented assumption, not a fudge of the numbers.

## 3. Architecture

```
                     pipeline/ (host, heavy CV)                app/ (lean container)
 CAM*.mp4 ─▶ detect.py ─▶ tracklets ─▶ associate.py ─▶ events.jsonl ─▶ POST /events/ingest ─▶ Postgres
            YOLOv8+ByteTrack  per-camera   sessions +        (8 types)        idempotent           │
            zones / line      summaries    re-ID + POS                         dedup               ▼
                                                                        metrics / funnel / heatmap /
                                                                        anomalies / health  ◀── dashboard
```

**Two-phase detection.** Phase 1 (`detect.py`) runs YOLOv8n + ByteTrack per camera
at 4–6 fps and summarises each track into a `Tracklet` (zone intervals, entry-line
crossings, billing queue depth, appearance histogram, staff signals). Phase 2
(`associate.py`) is **pure Python, cv2-free, and unit-tested** — it turns
tracklets into a coherent store-wide event stream. Separating them means the logic
the funnel depends on can be tested without a GPU or video.

**The API is event-sourced.** Every endpoint is computed live from the `events`
table via one shared read-model (`services/sessions.py`), so metrics, funnel and
heatmap can never disagree. There are no precomputed/cached aggregates — outputs
vary with input (passes the integrity check by construction).

## 4. How the seven edge cases are handled

| Edge case | Handling |
|---|---|
| **Group entry (3 together → 3)** | ByteTrack tracks each person; each track that crosses the entry line is one ENTRY. Tested in `test_pipeline.py`. |
| **Staff** | `is_staff` from: presence in back-room camera, time on the staff side of the billing counter, or dark-uniform + most-of-clip persistence. Optional VLM override. Excluded from every customer metric. |
| **Re-entry** | Exited sessions go to a gallery; an inbound crossing matching a recent exit (appearance + time) emits **REENTRY** reusing the `visitor_id` — no second ENTRY, no inflated visitor count. |
| **Partial occlusion** | Detection threshold kept low (0.15); low-confidence events are **stored and flagged via `confidence`**, never silently dropped. |
| **Billing queue** | On the billing camera, queue depth = #people in the queue region per frame; `BILLING_QUEUE_JOIN` carries it; leaving with no POS txn within 5 min → `BILLING_QUEUE_ABANDON`. |
| **Empty periods** | Zero-traffic returns valid zeroed JSON (`data_confidence: NO_DATA`), never null/crash. Tested. |
| **Camera overlap (double counting)** | Counting is anchored to the **entry camera only**; floor/billing detections are absorbed into existing sessions by appearance+time, so an overlapping floor view can't create a second visitor. |

## 5. The API (Part B/C) in brief

- **Window = "today" anchored to the freshest data** — the store-local day of the
  most recent event. This makes metrics behave like real-time for both historical
  replay and a live feed. `?date=` overrides.
- **Funnel** is session-based and **cumulative/nested** (reaching billing implies
  browsing) so it is monotonic by construction and robust to imperfect Re-ID.
- **Conversion** = unique non-staff visitors in the billing zone within 5 min
  before a POS transaction ÷ unique visitors.
- **Anomalies**: queue spike (depth thresholds), dead zone (no visit in 30 min,
  anchored to latest event), conversion drop vs trailing 7-day average — each with
  severity + a concrete `suggested_action`.
- **Production**: idempotent ingest, partial-success, structured JSON logs
  (`trace_id`, `store_id`, `endpoint`, `latency_ms`, `event_count`, `status_code`),
  DB-outage → 503 with no stack trace, multi-stage non-root Docker image, healthcheck.

## 6. AI-Assisted Decisions

AI tools (Claude) were used throughout. The decisions where they most shaped the
design — and where I **overrode** them:

1. **Cross-camera Re-ID — overrode.** The model first proposed an OSNet/torchreid
   appearance embedder for robust re-identification. I evaluated this against the
   constraints (4 GB laptop GPU, CPU-only torch in this environment, **fully
   blurred faces**) and rejected it: a deep Re-ID model is overkill and fragile
   here. I chose an **HSV torso-colour histogram** ("distance-based trajectory"
   approach the brief explicitly allows) and made the **entry camera the single
   source of truth** for counting, with cross-camera links treated as best-effort.
   This is honest about the ceiling on blurred CCTV and keeps counts trustworthy.

2. **Funnel definition — overrode.** The model suggested counting distinct visitors
   independently per stage (entry, zone, billing, purchase). With weak cross-camera
   linking that can produce a non-monotonic, confusing funnel. I changed it to a
   **cumulative nested** model (a session at billing is counted as having browsed),
   guaranteeing monotonic drop-off while staying session-based — see `funnel.py`.

3. **Health staleness — overrode.** The model measured feed lag from the latest
   *event timestamp*. That breaks for historical footage (always "stale"). I changed
   it to measure **ingestion recency** (`ingested_at`) — "is the feed flowing?" —
   which is what an on-call engineer actually needs and works for replay and live.

4. **VLM for staff detection — agreed, but gated.** The model suggested a VLM
   (Claude Vision) to classify staff vs customer. I **agreed it's promising** and
   implemented it (`pipeline/staff_vlm.py`, prompt shown there and in CHOICES.md),
   but made it **opt-in** with a rule-based fallback, because (a) it adds per-track
   latency/cost and (b) without an API key the pipeline must still run. My
   assessment of whether it beats the heuristic is in CHOICES.md.

## 7. Results on the provided clips (10 Apr 2026 excerpts)

Running `pipeline/run.sh` on the five real clips produced **140 schema-valid events
(0 duplicate ids)** across **18 customer sessions + 2 staff**, ingested into the API:

| Signal | Value |
|---|---|
| Entry-line crossings (CAM 3) | 11 (→ ENTRY/EXIT/REENTRY events) |
| Unique customers (in window) | 16 (staff excluded) |
| Conversion rate | **18.8%** (3 of 16, correlated to the real 20:25 POS txn) |
| Funnel | entered 16 → browsed 12 → billing 3 → purchased 3 |
| Zones with dwell | all five (SKINCARE, MAKEUP, MAKEUP_STUDIO, NAIL_FRAGRANCE, BILLING) |
| Staff detected | 2 (excluded from every customer metric) |

These are genuine pipeline outputs (not hand-set); a sample is committed as
`data/sample_events.jsonl`. **Honest caveat on entry direction:** the entry camera
is a tricky overhead view of a glass walkway. Foot-trajectory analysis
(`scripts/_diag_entry.py`) showed traffic crosses a *horizontal* line, which is how
the counting line is calibrated — but inbound-vs-outbound labelling on this angle is
uncertain, so the entry/exit *split* is less reliable than the *total* crossing
count. The funnel and visitor metrics deliberately count **sessions**, not raw ENTRY
events, so they are unaffected by that labelling ambiguity.

## 8. Known limitations / what breaks at 40 live stores

- Every analytics endpoint loads the day's events for a store into memory and
  aggregates in Python. Correct and fast for one store / short windows; at 40
  stores streaming continuously the **first thing to break is per-request
  aggregation latency and DB read load**. The fix is incremental rollups
  (materialised per-minute aggregates / a streaming aggregator) keyed by
  `(store_id, minute)` so endpoints read pre-aggregated rows — the event-sourced
  design makes that an additive change, not a rewrite.
- Cross-camera appearance matching is the weakest link; mis-merges/splits are
  possible. We surface `data_confidence` and never let it corrupt the entry count.
- Conversion via time-window POS correlation is inherently fuzzy when many visitors
  cluster near one transaction (multiple "converted" for one sale). Documented; the
  honest fix would need basket-to-person attribution we don't have.

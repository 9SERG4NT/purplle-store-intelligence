# Apex Retail — Store Intelligence System

End-to-end pipeline that turns raw in-store CCTV into a **real-time offline-store
conversion-rate** analytics API. Raw clips → detection + tracking → structured
events → ingestion API → live dashboard.

> **North-star metric:** Offline conversion rate = converted unique visitors ÷
> unique visitors. A visitor is *converted* if they were in the billing zone in
> the 5 minutes before a POS transaction.

```
 CCTV clips ──▶ Detection layer ──▶ Event stream ──▶ Intelligence API ──▶ Live dashboard
 (5 cameras)    YOLOv8 + ByteTrack   JSONL events     FastAPI + Postgres    web (polling)
                Re-ID · zones ·      (8 event types)  metrics / funnel /
                staff · queue                          heatmap / anomalies / health
```

The detection pipeline (heavy CV deps) runs on the **host**; the API ships as a
**lean container**. Events flow between them over `POST /events/ingest`.

---

## Quickstart (5 commands)

```bash
# 1. clone
git clone <REPO_URL> && cd store-intelligence

# 2. start everything: Postgres + API + a one-shot replay that streams the
#    bundled sample events so the dashboard is live immediately
docker compose up --build
```

Open:
- **Dashboard:** http://localhost:8000/dashboard/
- **API docs (Swagger):** http://localhost:8000/docs
- **Metrics:** http://localhost:8000/stores/STORE_BLR_002/metrics

To run the **real detection pipeline** against the CCTV clips and feed its output
into the running API:

```bash
# 3. install pipeline deps (host; not in the API image)
python -m pip install -r pipeline/requirements.txt

# 4. process all clips -> data/events.jsonl  (+ normalised data/pos_transactions.csv)
FOOTAGE="../CCTV Footage" RAW_POS="../Brigade_Bangalore_10_April_26 (1)bc6219c.csv" bash pipeline/run.sh

# 5. stream the real events into the API in simulated real time (watch the dashboard)
python scripts/replay.py --api http://localhost:8000 --speed 30
```

> The clips/POS are **not** in the repo (challenge rule). Point `FOOTAGE` and
> `RAW_POS` at wherever you extracted the dataset. Defaults assume it sits one
> directory above the repo.

### Data provenance (what's committed vs generated)

| File | Committed? | What it is |
|---|---|---|
| `data/sample_events.jsonl` | ✅ | **Real** pipeline output (140 events) from the 5 clips — the dashboard seed & test reference. |
| `data/store_layout.json` | ✅ | Hand-authored zones / entry line (config, not dataset). |
| `data/demo_pos.csv` | ✅ | **Synthetic** POS, time-aligned to the real billing events so a clean clone still shows conversion (avoids redistributing real transaction data). |
| `data/pos_transactions.csv` | ❌ git-ignored | **Real** anonymised POS — regenerate with `python pipeline/pos.py <raw.csv> data/pos_transactions.csv`. The API prefers it when present, else falls back to `demo_pos.csv`. |
| `data/events.jsonl`, footage, raw POS | ❌ git-ignored | Raw dataset / full pipeline output. |

---

## What each stage does

| Stage | Where | Notes |
|---|---|---|
| **Detection + tracking** | `pipeline/detect.py` | YOLOv8 person detection + ByteTrack per camera, sampled at 4–6 fps. |
| **Re-ID / sessions** | `pipeline/associate.py` | Entry-line counting, re-entry dedup, cross-camera linking, staff classification → one event stream. |
| **Event schema** | `pipeline/emit.py`, `app/schemas.py` | 8 event types; one shared contract. |
| **Ingestion** | `app/services/ingestion.py` | Batch ≤ 500, idempotent by `event_id`, partial success. |
| **Analytics** | `app/services/{metrics,funnel,heatmap,anomalies}.py` | Session-based, staff-excluded, POS-correlated. |
| **Dashboard** | `dashboard/` | Polls the API every 1.5 s; live conversion / funnel / queue / anomalies. |

## API endpoints

| Method | Path | Returns |
|---|---|---|
| `POST` | `/events/ingest` | Validate + dedup + store a batch (≤500). Idempotent, partial-success. |
| `GET` | `/stores/{id}/metrics` | Unique visitors, conversion rate, dwell/zone, queue depth, abandonment. |
| `GET` | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase, session-based, with drop-off %. |
| `GET` | `/stores/{id}/heatmap` | Per-zone visits + dwell, normalised 0–100, `data_confidence`. |
| `GET` | `/stores/{id}/anomalies` | Queue spike, dead zone, conversion drop — severity + suggested action. |
| `GET` | `/health` | DB status, per-store last event/ingest, `stale_feed`. |

## Tests

```bash
python -m pip install -r requirements.txt
python -m pytest --cov          # 34 tests, ~93% statement coverage
```

Edge cases covered: empty store, all-staff clip, zero purchases, re-entry in the
funnel, idempotent re-ingest, malformed-event partial success, batch > 500, and
graceful 503 on DB outage.

## Calibrating zones to new footage

```bash
python scripts/calibrate_zones.py --footage "../CCTV Footage"   # overlays zones on frames
```
Edit the normalised polygons / entry line in `data/store_layout.json`.

## Documentation

- [`docs/DESIGN.md`](docs/DESIGN.md) — architecture + AI-assisted decisions.
- [`docs/CHOICES.md`](docs/CHOICES.md) — model, schema, and storage trade-offs.

## Layout

```
store-intelligence/
├── pipeline/        detection + tracking + association + POS normalisation
├── app/             FastAPI service (core/, services/, api/)
├── dashboard/       live web dashboard
├── scripts/         replay.py, calibrate_zones.py, make_demo_data.py
├── tests/           pytest suite (prompt blocks at the top of each file)
├── data/            store_layout.json, pos_transactions.csv, sample_events.jsonl
├── docs/            DESIGN.md, CHOICES.md
├── Dockerfile       multi-stage, non-root, healthcheck
└── docker-compose.yml
```

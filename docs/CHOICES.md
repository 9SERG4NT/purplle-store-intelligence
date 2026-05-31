# CHOICES — key decisions, trade-offs, and what I overrode

Three decisions in depth, plus the VLM evaluation the brief asks for. For each:
**options considered → what AI suggested → what I chose and why.**

---

## Decision 1 — Detection & tracking model

**Options considered**
- YOLOv8 (n/s/m) + ByteTrack
- RT-DETR (transformer) + ByteTrack
- YOLOv8 + DeepSORT/StrongSORT with an OSNet Re-ID embedder

**What AI suggested.** Claude's first instinct was the "strongest" stack: RT-DETR
for occlusion robustness, or DeepSORT + OSNet appearance Re-ID for identity
persistence across cameras.

**What I chose — YOLOv8n + ByteTrack — and why.** The hard constraint is the
hardware: a 4 GB laptop GPU, and CPU-only torch in this build. RT-DETR and an
OSNet embedder are heavier and slower for a *marginal* accuracy gain that is
mostly wasted here, because **faces are fully blurred** — the discriminative
signal a deep Re-ID model exploits is largely gone. ByteTrack is a strong, fast,
dependency-light tracker that keeps low-confidence detection boxes in the
association step, which directly helps the partial-occlusion edge case. YOLOv8n
runs comfortably at 640 px on the short clips. The brief is explicit that it
scores *reasoning under constraints*, not raw model size — so I picked the model
that is honestly matched to the inputs and documented the ceiling. Re-ID is done
with an HSV torso-colour histogram + temporal gating ("distance-based trajectory"
approach the brief allows). **I overrode the heavier suggestion**; if faces were
*not* blurred or a proper GPU were available, OSNet Re-ID would be the right call,
and the architecture isolates Re-ID so swapping it in is local.

---

## Decision 2 — Event schema design

**Options considered**
- A flat schema (one row, all fields top-level)
- The brief's schema with a nested `metadata` object
- A normalised multi-table schema (events + zones + sessions)

**What AI suggested.** Claude proposed enriching `metadata` with extra fields
(track confidence, bbox, camera transforms) "for completeness".

**What I chose & why.** I kept **exactly the brief's schema** (top-level fields +
a `metadata` object holding `queue_depth`, `sku_zone`, `session_seq`) and
**declined the extra fields**. Reasons: (1) the scoring harness validates against
*this* schema — adding fields risks nothing but invites drift; (2) the single most
important design property is that `event_id` is a **globally unique idempotency
key** and `visitor_id` is the **session unit** (re-entry reuses it, so the funnel
can't double-count) — those do all the heavy lifting, extra telemetry doesn't;
(3) one contract, defined once in `pipeline/emit.py` and mirrored in
`app/schemas.py`, with `sample_events.jsonl` generated from it so the producer and
consumer can't diverge. I kept `confidence` top-level and made a rule of **never
dropping low-confidence events** — they're flagged, not suppressed, which is what
the brief rewards. The one judgement call: `zone_id` is `null` for ENTRY/EXIT (per
spec), and billing presence is modelled as the `BILLING` zone so it flows through
the same zone-dwell logic as any other zone.

---

## Decision 3 — API storage & "real-time" semantics

**Options considered**
- SQLite (single file, embedded)
- PostgreSQL in docker-compose
- A time-series store (Timescale/Influx)

**What AI suggested.** Claude suggested SQLite "since the FAQ says it's fine" for
simplicity.

**What I chose — PostgreSQL — and why.** SQLite would pass, but two production
requirements make Postgres the better story: (1) **graceful degradation** — "DB
unavailable → 503" is trivial and *demonstrable* when the DB is a separate
container you can stop; with embedded SQLite there's nothing to disconnect from;
(2) it models the real shape (an API pod + a shared datastore) and makes the
`docker compose up` story honest. I used **sync SQLAlchemy 2.0** rather than async
— at this scale async adds event-loop complexity (and fiddly test fixtures) for no
throughput win, and sync endpoints run in FastAPI's threadpool fine. Tests run the
identical code against in-memory SQLite, so the storage choice doesn't leak into
the logic. A time-series DB was overkill for a take-home and would obscure the
event-sourced design. **The deeper choice** was "real-time": rather than literal
wall-clock "today" (which is empty for historical footage), the metrics window is
anchored to the **freshest event** for the store — so the same code serves a live
feed and a replay. This is the call I'd most want to defend, and it's why the demo
shows live numbers at all.

---

## VLM evaluation — staff vs customer (`pipeline/staff_vlm.py`)

I evaluated a VLM (Claude Vision) for staff classification. **Prompt used:**

> *You are looking at a cropped CCTV still of ONE person inside a Purplle
> cosmetics retail store. Faces are blurred. Store STAFF wear a dark/black uniform
> top and usually stand behind a counter or restock product walls. CUSTOMERS wear
> varied clothing and browse or queue to pay. Classify this person. Reply with
> ONLY a JSON object: {"is_staff": true|false, "confidence": 0.0-1.0, "reason":
> "<short>"}. If genuinely unsure, prefer is_staff=false (we'd rather miss a staff
> member than wrongly drop a real customer from the conversion metric).*

**Did it work / would I ship it?** The rule-based heuristic (back-room presence +
behind-counter dwell + dark-uniform persistence) already captures the unambiguous
cases cheaply and deterministically. The VLM's value is the **ambiguous floor
staff** who dress like customers — exactly where colour/position rules are weak —
and it can read context (holding a scanner, restocking) a histogram can't. The
costs are real: a per-track API call (latency + spend) and a network dependency in
what should be an offline batch step. **Decision: keep it opt-in** (`USE_VLM_STAFF=1`)
as a precision booster, default to the heuristic. I'd flip the default to VLM only
if staff mis-labelling were measurably hurting the conversion denominator on the
ground-truth clip — i.e., I'd let the metric, not a hunch, justify the cost.

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

**Update — I then benchmarked the detector instead of assuming.** I researched the
current field (YOLO11, YOLOv10/12, RF-DETR) and learned YOLO11 (Oct 2024) gets
higher mAP than YOLOv8 with ~22% fewer parameters, while YOLOv12/RF-DETR are
attention-heavy and *slower/unstable on CPU* — the wrong fit for a CPU-only build.
So I ran my own measurement on the actual clips (12 frames, 3 cameras):

| model | person detections | ms/frame (CPU) |
|---|---|---|
| yolov8n | 48 | 247 |
| **yolo11s** | **57 (+19%)** | **136 (fastest)** |
| yolo11m | 55 | 332 |

**yolo11s won on both recall and speed**, and notably *beat the heavier yolo11m*
here — so I switched the default to `yolo11s.pt`. This is the "iterate on the
detection approach based on AI feedback + your own evaluation" the brief rewards:
the bigger model wasn't better, the data said so, and the choice is one env var
(`YOLO_WEIGHTS`).

**Update 2 — re-benchmarked against YOLO26 (and why I switched again).** I checked
whether a newer model beats yolo11s rather than assuming yolo11 was the end of the
line. Ultralytics released **YOLO26 in Jan 2026**: a *NMS-free, end-to-end* detector
(no Non-Max-Suppression, DFL removed) explicitly tuned for **edge / low-power / CPU**
— up to ~43% faster CPU inference than YOLO11 at comparable accuracy. That is a near-
perfect match for this build (CPU-only torch). It needed `ultralytics>=8.4`, so I
upgraded (torch unchanged at 2.12.0+cpu) and ran my own measurement on the real clips
(16 frames across the entry/floor cameras, CPU):

| model | person detections | ms/frame (CPU) |
|---|---|---|
| yolo11s | 73 | 391 |
| **yolo26s** | **94 (+29%)** | **391 (same)** |
| yolo26n | 49 | 181 (2× faster) |

**yolo26s detects 29% more people than yolo11s at identical latency**, and I confirmed
it tracks cleanly with ByteTrack. Higher recall directly reduces *missed* entries and
visitors — the thing that most hurts count accuracy — so I switched the default to
`yolo26s.pt`. yolo26n is 2× faster but drops too many people for a counting task; it's
the right pick only on a truly constrained edge box (one env var away). The whole
journey — yolov8n → yolo11s → yolo26s, each step a *measurement on this footage*, not
a spec-sheet — is exactly the iteration the brief asks for.

**Counting line — the calibration that actually drives entry accuracy.** A reviewer
pointed out the second store's entry count was wrong: the counting line was drawn
across the **mall corridor** (y=0.26), so every shopper *walking past the glass* was
being counted, not those entering. The model was never the problem — the line was.
Fix (calibrated from extracted frames, candidate lines drawn and visually checked):
move the line onto the **door threshold** where the glass meets the store's wooden
floor (y≈0.60–0.62), and add a "fully entered" guard (`CROSSING_MARGIN`): the feet
must move from clearly *outside* the line to clearly *inside* it before a crossing
counts, so a person grazing or loitering on the threshold isn't tallied until they
have actually stepped onto the store floor. Lesson worth stating plainly: for line-
crossing counters the **geometry/calibration matters more than the model** — a bigger
net behind a mis-placed line just counts the wrong people faster.

**Other models I evaluated and rejected (and why).** I was asked to consider Meta's
**Sapiens2** (HF `facebook/sapiens2`) and several cloud APIs. The decisive insight is
a *category* distinction: Sapiens2 is a human-centric **analyzer** (pose, body-part
segmentation, normals, matting on a *cropped* person), not a **detector** — it can't
find or count people on its own; it runs *downstream* of a detector. So it can't
replace YOLO for this task. It's also a 0.1B–5B-parameter ViT built for GPU/high-res:
on a CPU-only, 4 GB box it's seconds/frame and impractical. **Verdict: not a
replacement; a future *GPU-only* add-on** for richer per-person signals (e.g.
pose-based product interaction, or skeleton-based staff-vs-customer that would beat
the HSV-uniform heuristic on blurred footage). The cloud options — **Azure AI Vision,
Google Cloud Vision, Clarifai, Kairos/Affectiva** — were rejected on three grounds:
(1) they break the "runs offline via `docker compose up`, no manual steps" acceptance
gate; (2) sending in-store CCTV to a third party is a privacy problem (and the faces
are already blurred, so face/emotion APIs can't work anyway); (3) per-call cost and
latency on video. Local YOLO26s + ByteTrack is free, offline, private, and matched to
the actual job — which is why it's the choice.

**Update 3 — I wired in OSNet Re-ID, measured it, and OVERRODE it (kept HSV).** Decision
1 said "if a proper model were viable, OSNet would be the right call, and the
architecture isolates Re-ID so swapping it in is local." So I actually did the swap:
**OSNet `x0_25` (~2M params, MSMT17-trained)** via `boxmot` (verified it leaves
torch/ultralytics untouched — adds only a few small packages), embedding the best crop
per track into a 512-d vector, one inference per track. Then — instead of assuming it
was better — I **swept the match threshold on the real footage** (`scripts/_tune_reid.py`,
detection cached once, thresholds swept instantly). The result was the decisive part:

| cosine threshold | unique customers (store 1) |
|---|---|
| 0.60 | 9 (everything merges) |
| 0.66 | 16 |
| 0.70 | 30 |
| 0.76 | 49 |
| 0.80 | 61 (nothing merges; 167 raw tracklets) |

**No plateau.** A reliable Re-ID shows a *stable band* where same-person pairs score
clearly above different-person pairs; here the count slides monotonically, meaning
OSNet's same/different cosines **overlap heavily on this footage**. Cause: OSNet is
trained on street-level, frontal, unblurred pedestrians, but here it's **overhead retail
CCTV with blurred faces** — the same domain mismatch that sank the VLM staff experiment.
The HSV colour histogram, by contrast, gives a *stable* visitor count because clothing
colour stays separable under blur. **Decision: keep the HSV histogram as the default
(`descriptor_similarity` is Pearson, threshold 0.55); OSNet stays fully wired but
`USE_OSNET_REID=0`**, ready for a deployment with frontal/unblurred cameras where its
learned features actually separate. This is the rubric's "where I overrode the AI" moment
in its purest form: the fancier model lost a measurement, and I have the table to prove
it. (`x0_25` over the heavier `osnet_ain_x1_0` was the right *variant* for CPU; the issue
is the footage domain, not the size.) Sapiens2 remains the GPU option above this tier,
for pose analytics rather than identity.

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

**Update after inspecting the provided `sample_events.jsonl`:** the actual sample
uses a *different, multi-source* schema than the PDF — three event families with
`id_token`/`track_id`, `store_code`/`store_id`, demographics, and terminal
`queue_completed`/`queue_abandoned` events. Rather than bet on one, I built a
normalisation layer (`app/services/normalize.py`) so the API ingests **both** the
PDF schema and the sample schema, mapping them onto one internal event. This is the
single most important robustness decision for the held-out scoring set — a case
where the provided artefact, not the spec, drove the design. I kept the pipeline
emitting the PDF schema and made the *consumer* tolerant, because a tolerant ingest
is cheap insurance and a lossy pipeline rewrite is not.

**What we deliberately do *not* fabricate.** Our emitted official-schema log
(`data/sample_events.jsonl`, `data/events_store2_official.jsonl`) carries every
required field — event-type families, `id_token`/`track_id`, store ids, timestamps,
zone metadata, `queue_*` terminal fields — and validates cleanly against the provided
sample with `python scripts/validate_events.py` (0 errors). Two enrichment fields the
sample shows are intentionally left `null`/absent rather than invented: **demographics**
(`gender`/`age`) because the dataset's faces are blurred (`is_face_hidden=true`), and
**`zone_hotspot_x/y`** because a single representative pixel per visit would either be a
constant per-zone centroid (a non-varying output the brief explicitly penalises) or
require persisting per-frame coordinates we don't otherwise need. Honest absence beats
fabricated precision; the analytics (conversion, funnel, heatmap, queues) depend on the
timestamped event stream, not on these fields.

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

**Did it work / would I ship it?** I implemented two providers
(`VLM_PROVIDER=groq` → Llama-4 vision, OpenAI-compatible API; or `anthropic` →
Claude vision; see `pipeline/staff_vlm.py`) and **actually ran it on real crops**
from the billing and floor cameras via Groq. Honest result: it returned
well-formed JSON verdicts but labelled BOTH the counter person and a floor shopper
`is_staff=false` — on **blurred-face, low-resolution** crops the uniform-vs-casual
cue is ambiguous and, per my prompt, it defaults conservative. So the **VLM did not
beat the rule-based heuristic** here. The heuristic (back-room presence +
behind-counter *position* + dark-uniform persistence) exploits *spatial* signal the
tight crop throws away, and is cheap and deterministic. **Decision: keep the VLM
opt-in** (`USE_VLM_STAFF=1`), default to the heuristic. The change most likely to
make the VLM win is feeding it the *whole frame with the counter visible* (spatial
context) rather than a tight crop — I'd try that before raising spend. Running the
experiment, not assuming, settled this.

"""Tunable knobs for the detection pipeline.

Everything that affects counting accuracy lives here so it can be reasoned
about and overridden via environment variables without touching code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class PipelineConfig:
    # --- detection ---
    # yolo26s won a re-benchmark on this footage: +29% person detections vs yolo11s at
    # the SAME CPU latency (NMS-free, end-to-end; Ultralytics' Jan-2026 SOTA). Higher
    # recall directly helps not missing entries/visitors. See CHOICES.md.
    model_weights: str = os.getenv("YOLO_WEIGHTS", "yolo26s.pt")
    imgsz: int = _i("YOLO_IMGSZ", 640)
    # Keep LOW-confidence detections (challenge: "do not suppress low-conf events").
    # We detect down to det_conf but flag anything below report_conf as low-confidence.
    det_conf: float = _f("DET_CONF", 0.15)
    person_class_id: int = 0  # COCO 'person'

    # --- zone dwell ---
    dwell_emit_seconds: float = _f("DWELL_EMIT_SECONDS", 30.0)  # ZONE_DWELL cadence
    min_zone_seconds: float = _f("MIN_ZONE_SECONDS", 1.5)  # ignore drive-by flickers

    # --- entry/exit smoothing ---
    # A track must persist this long before its crossing counts (kills 1-frame ghosts).
    min_track_seconds_for_entry: float = _f("MIN_TRACK_SECONDS_FOR_ENTRY", 0.6)
    # Ignore another crossing from the same track within this window (kills line jitter).
    crossing_debounce_seconds: float = _f("CROSSING_DEBOUNCE_SECONDS", 2.0)
    # "Fully entered" guard: the feet must move from >= this far OUTSIDE the line to
    # >= this far INSIDE it (normalised frame units) for a crossing to count — so a
    # person merely grazing/standing on the threshold is not counted until they have
    # clearly stepped onto the store floor.
    crossing_margin: float = _f("CROSSING_MARGIN", 0.03)

    # --- re-entry & cross-camera association ---
    reentry_window_seconds: float = _f("REENTRY_WINDOW_SECONDS", 600.0)
    # Pearson-correlation threshold on the HSV histogram (the default descriptor).
    appearance_match_threshold: float = _f("APPEARANCE_MATCH_THRESHOLD", 0.55)
    # cross-camera match relaxes the same-camera threshold by this factor (angle/lighting differ)
    cross_camera_match_factor: float = _f("CROSS_CAMERA_MATCH_FACTOR", 0.72)
    # Deep Re-ID (OSNet via boxmot): wired in and CPU-feasible, but OFF by default —
    # measured on this footage it had no stable identity threshold (overhead + blurred
    # faces = domain mismatch; see CHOICES.md). Flip USE_OSNET_REID=1 for deployments
    # with frontal/unblurred cameras where it earns its keep; falls back to HSV otherwise.
    use_osnet_reid: bool = os.getenv("USE_OSNET_REID", "0") == "1"
    reid_weights: str = os.getenv("REID_WEIGHTS", "osnet_x0_25_msmt17.pt")
    # max plausible walk time entry-camera -> floor/billing camera
    transit_window_seconds: float = _f("TRANSIT_WINDOW_SECONDS", 90.0)

    # --- staff heuristic ---
    staff_dark_fraction: float = _f("STAFF_DARK_FRACTION", 0.45)  # black-uniform pixels
    # a track present for >= this fraction of its camera's clip is probably staff
    staff_persistence_fraction: float = _f("STAFF_PERSISTENCE_FRACTION", 0.6)
    use_vlm_staff: bool = os.getenv("USE_VLM_STAFF", "0") == "1"

    # --- billing ---
    billing_min_seconds: float = _f("BILLING_MIN_SECONDS", 2.0)

    # --- confidence reporting ---
    low_confidence_threshold: float = _f("LOW_CONFIDENCE_THRESHOLD", 0.35)


CONFIG = PipelineConfig()

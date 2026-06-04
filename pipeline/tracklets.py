"""Plain (cv2-free) data structures shared between detection and association.

Keeping these dependency-light means the association logic — the part the
funnel correctness depends on — can be unit-tested without video, models, or a
GPU.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ZoneInterval:
    zone_id: str
    department: str
    t_enter: datetime
    t_exit: datetime
    camera_id: str = ""
    queue_depth_at_join: Optional[int] = None  # only set for BILLING

    @property
    def dwell_seconds(self) -> float:
        return max(0.0, (self.t_exit - self.t_enter).total_seconds())


@dataclass
class Crossing:
    t: datetime
    direction: str  # "inbound" | "outbound"


@dataclass
class Tracklet:
    """One ByteTrack track on one camera, summarised after the frame loop."""

    camera_id: str
    role: str
    local_track_id: int
    t_start: datetime
    t_end: datetime
    n_frames: int
    conf_mean: float
    descriptor: Optional[list[float]] = None
    dark_fraction: float = 0.0
    behind_counter_frac: float = 0.0      # billing cam: time on the staff side of counter
    in_backroom: bool = False
    clip_fraction: float = 0.0            # fraction of the clip this track was present
    zone_intervals: list[ZoneInterval] = field(default_factory=list)
    crossings: list[Crossing] = field(default_factory=list)
    vlm_is_staff: Optional[bool] = None  # set only when the optional VLM classifier ran

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.t_end - self.t_start).total_seconds())


def descriptor_similarity(a: Optional[list[float]], b: Optional[list[float]]) -> float:
    """Pearson correlation of two descriptor vectors, clamped to [0, 1].

    Mirrors OpenCV's HISTCMP_CORREL for the default HSV colour histogram, in pure
    numpy-free Python so the association engine has no binary dependencies. (Also
    serves the opt-in OSNet embedding path — Pearson is mean-centred cosine.)

    Chosen over plain cosine after measuring both on this footage: the HSV
    histogram gives a stable visitor count, whereas the OSNet embedding had no
    clean same/different separation on overhead, blurred-face crops (count swung
    9->61 across thresholds with no plateau) — see CHOICES.md.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    n = len(a)
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((a[i] - ma) ** 2 for i in range(n)))
    db = math.sqrt(sum((b[i] - mb) ** 2 for i in range(n)))
    if da == 0 or db == 0:
        return 0.0
    return max(0.0, min(1.0, num / (da * db)))

"""Appearance descriptors for Re-ID and a colour-based staff heuristic.

Faces are blurred in this footage, so face-based Re-ID is impossible. We use a
cheap, robust clothing-colour signature instead: an HSV histogram of the torso
region. This is the "distance-based approach" the brief explicitly allows and is
the honest ceiling for blurred-face CCTV at 4 GB VRAM. Its failure modes (two
people in similar clothing) are documented in CHOICES.md.
"""
from __future__ import annotations

import cv2
import numpy as np

# HSV histogram bins. Hue dominates clothing identity; keep S/V coarse.
_H_BINS, _S_BINS, _V_BINS = 24, 4, 4


def torso_crop(frame: np.ndarray, xyxy) -> np.ndarray | None:
    """Upper-central portion of the person box (shoulders -> waist).

    Avoids the head (blurred, low info) and the legs (often occluded by fixtures).
    """
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in xyxy]
    bw, bh = x2 - x1, y2 - y1
    if bw < 6 or bh < 12:
        return None
    tx1 = max(0, int(x1 + 0.20 * bw))
    tx2 = min(w, int(x2 - 0.20 * bw))
    ty1 = max(0, int(y1 + 0.18 * bh))
    ty2 = min(h, int(y1 + 0.55 * bh))
    if tx2 <= tx1 or ty2 <= ty1:
        return None
    return frame[ty1:ty2, tx1:tx2]


def appearance_descriptor(frame: np.ndarray, xyxy) -> np.ndarray | None:
    """Normalised HSV histogram of the torso, flattened to a 1-D float32 vector."""
    crop = torso_crop(frame, xyxy)
    if crop is None or crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist(
        [hsv], [0, 1, 2], None,
        [_H_BINS, _S_BINS, _V_BINS],
        [0, 180, 0, 256, 0, 256],
    )
    cv2.normalize(hist, hist, alpha=1.0, norm_type=cv2.NORM_L1)
    return hist.flatten().astype(np.float32)


def similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """Histogram-correlation similarity in [0, 1] (clamped)."""
    if a is None or b is None:
        return 0.0
    score = float(cv2.compareHist(a.astype(np.float32), b.astype(np.float32), cv2.HISTCMP_CORREL))
    return max(0.0, min(1.0, score))


def dark_fraction(frame: np.ndarray, xyxy) -> float:
    """Fraction of torso pixels that are dark (black uniform).

    Purplle floor staff wear black tops; this is one (weak, documented) signal
    feeding the staff classifier. Returns 0.0 when the crop is unusable.
    """
    crop = torso_crop(frame, xyxy)
    if crop is None or crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    dark = (v < 70) & (s < 90)  # low value + low saturation == near-black
    return float(dark.mean())

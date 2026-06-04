"""Optional deep Re-ID embeddings (OSNet) for stronger identity matching.

YOLO finds and tracks people; this turns the *best* crop of each track into a
512-d appearance embedding (OSNet x0_25, MSMT17-trained, via boxmot). It runs on
CPU — one embed per track, not per frame — and is a big step up from the HSV
colour histogram for cross-camera dedup and re-entry, the two places the HSV
signature is weakest (similar clothing, different lighting).

Fully optional and lazy: if boxmot or the weights aren't present, `embed_crop`
returns None and callers fall back to the histogram descriptor, so the pipeline
still runs with zero extra dependencies. The model loads once (singleton).
"""
from __future__ import annotations

import numpy as np

from config import CONFIG

_MODEL = None
_TRIED = False


def _get_model():
    """Lazily build the OSNet backend once; None if unavailable/disabled."""
    global _MODEL, _TRIED
    if _TRIED:
        return _MODEL
    _TRIED = True
    if not CONFIG.use_osnet_reid:
        return None
    try:
        from boxmot.reid import ReID  # heavy import; only when actually used
        _MODEL = ReID(weights=CONFIG.reid_weights, device="cpu")
        print(f"[reid] OSNet loaded ({CONFIG.reid_weights})", flush=True)
    except Exception as e:  # boxmot missing, weight download failed, etc.
        print(f"[reid] OSNet unavailable ({e}); using HSV histogram fallback", flush=True)
        _MODEL = None
    return _MODEL


def embed_crop(crop: np.ndarray | None) -> list[float] | None:
    """L2-normalised OSNet embedding of a BGR person crop, as a plain list.

    Returns None when Re-ID is disabled/unavailable or the crop is too small,
    which signals the caller to fall back to the histogram descriptor.
    """
    m = _get_model()
    if m is None or crop is None or getattr(crop, "size", 0) == 0:
        return None
    h, w = crop.shape[:2]
    if h < 8 or w < 8:
        return None
    box = np.array([[0, 0, w, h]], dtype=float)  # whole crop is the person
    try:
        feat = np.asarray(m.model.get_features(box, crop))[0]
    except Exception:
        return None
    norm = float(np.linalg.norm(feat))
    if norm == 0:
        return None
    return (feat / norm).astype(float).tolist()

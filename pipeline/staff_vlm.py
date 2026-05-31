"""OPTIONAL: staff vs customer classification with a VLM (Claude Vision).

Off by default (USE_VLM_STAFF=1 to enable). When enabled, detect.py saves the
largest crop per track to data/_vlm_crops/. This module sends each crop to
Claude and overrides the rule-based `is_staff` with the model's verdict.

The exact prompt is below and is reproduced in DESIGN.md together with an
honest assessment of whether it beat the colour/position heuristic. Requires
ANTHROPIC_API_KEY; gracefully no-ops without it so a normal run is unaffected.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

VLM_MODEL = os.getenv("VLM_MODEL", "claude-opus-4-8")

PROMPT = (
    "You are looking at a cropped CCTV still of ONE person inside a Purplle "
    "cosmetics retail store. Faces are blurred. Store STAFF wear a dark/black "
    "uniform top and usually stand behind a counter or restock product walls. "
    "CUSTOMERS wear varied clothing and browse or queue to pay.\n"
    "Classify this person. Reply with ONLY a JSON object:\n"
    '{"is_staff": true|false, "confidence": 0.0-1.0, "reason": "<short>"}\n'
    "If genuinely unsure, prefer is_staff=false (we would rather miss a staff "
    "member than wrongly drop a real customer from the conversion metric)."
)


def _classify_one(image_bytes: bytes, client) -> bool | None:
    b64 = base64.standard_b64encode(image_bytes).decode()
    msg = client.messages.create(
        model=VLM_MODEL,
        max_tokens=150,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    text = "".join(getattr(b, "text", "") for b in msg.content)
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        return bool(json.loads(text[start:end]).get("is_staff"))
    except (ValueError, json.JSONDecodeError):
        return None


def classify_tracklets_with_vlm(tracklets, footage_dir, layout) -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("[vlm] ANTHROPIC_API_KEY not set -> keeping rule-based staff labels")
        return
    try:
        import anthropic
    except ImportError:
        print("[vlm] `anthropic` not installed -> keeping rule-based staff labels")
        return

    crop_dir = Path(__file__).resolve().parent.parent / "data" / "_vlm_crops"
    client = anthropic.Anthropic()
    n = 0
    for t in tracklets:
        crop = crop_dir / f"{t.camera_id}__{t.local_track_id}.jpg"
        if not crop.exists():
            continue
        verdict = _classify_one(crop.read_bytes(), client)
        if verdict is not None:
            t.vlm_is_staff = verdict
            n += 1
    print(f"[vlm] classified {n} tracks with {VLM_MODEL}")

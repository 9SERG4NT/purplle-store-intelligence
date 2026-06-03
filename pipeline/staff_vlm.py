"""OPTIONAL: staff vs customer classification with a VLM.

Off by default (USE_VLM_STAFF=1 to enable). Supports two providers via
VLM_PROVIDER: "groq" (Llama-4 vision, OpenAI-compatible API) or "anthropic"
(Claude vision). When enabled, detect.py saves the largest crop per track to
data/_vlm_crops/ and this module overrides the rule-based is_staff with the
model's verdict.

The exact prompt is below and is reproduced in DESIGN.md/CHOICES.md with an
honest assessment of whether it beat the colour/position heuristic. Requires the
matching API key; gracefully no-ops without it so a normal run is unaffected.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

PROVIDER = os.getenv("VLM_PROVIDER", "groq").lower()
GROQ_MODEL = os.getenv("GROQ_VLM_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
ANTHROPIC_MODEL = os.getenv("VLM_MODEL", "claude-opus-4-8")

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


def _parse_verdict(text: str) -> bool | None:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        return bool(json.loads(text[start:end]).get("is_staff"))
    except (ValueError, json.JSONDecodeError):
        return None


def classify_one(image_bytes: bytes) -> bool | None:
    """Classify a single JPEG crop. Returns True/False or None on failure."""
    b64 = base64.standard_b64encode(image_bytes).decode()
    if PROVIDER == "groq":
        return _groq(b64)
    return _anthropic(b64)


def _groq(b64: str) -> bool | None:
    import httpx

    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": GROQ_MODEL,
            "temperature": 0,
            "max_tokens": 150,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
        },
        timeout=30.0,
    )
    r.raise_for_status()
    return _parse_verdict(r.json()["choices"][0]["message"]["content"])


def _anthropic(b64: str) -> bool | None:
    try:
        import anthropic
    except ImportError:
        return None
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=ANTHROPIC_MODEL, max_tokens=150,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": PROMPT},
        ]}],
    )
    return _parse_verdict("".join(getattr(b, "text", "") for b in msg.content))


def classify_tracklets_with_vlm(tracklets, footage_dir, layout) -> None:
    has_key = os.getenv("GROQ_API_KEY") if PROVIDER == "groq" else os.getenv("ANTHROPIC_API_KEY")
    if not has_key:
        print(f"[vlm] no key for provider={PROVIDER} -> keeping rule-based staff labels")
        return
    crop_dir = Path(__file__).resolve().parent.parent / "data" / "_vlm_crops"
    n = 0
    for t in tracklets:
        crop = crop_dir / f"{t.camera_id}__{t.local_track_id}.jpg"
        if not crop.exists():
            continue
        verdict = classify_one(crop.read_bytes())
        if verdict is not None:
            t.vlm_is_staff = verdict
            n += 1
    print(f"[vlm] classified {n} tracks via {PROVIDER} ({GROQ_MODEL if PROVIDER=='groq' else ANTHROPIC_MODEL})")

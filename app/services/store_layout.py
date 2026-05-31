"""Lightweight store-layout access for the API (known stores, zone -> department).

Independent of the pipeline package so the API image never needs cv2/torch.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings


@lru_cache
def _layout() -> dict:
    path = Path(get_settings().store_layout_path)
    if not path.exists():
        return {"store_id": "STORE_BLR_002", "timezone": "Asia/Kolkata", "zones": []}
    return json.loads(path.read_text(encoding="utf-8"))


def known_store_ids() -> list[str]:
    return [_layout().get("store_id", "STORE_BLR_002")]


def store_timezone(store_id: str) -> str:
    return _layout().get("timezone", "Asia/Kolkata")


def is_known_store(store_id: str) -> bool:
    return store_id in known_store_ids()


def analytics_zones() -> list[dict]:
    """Named zones that count for heatmap / dead-zone (exclude ENTRY threshold)."""
    out = []
    seen = set()
    for z in _layout().get("zones", []):
        zid = z["zone_id"]
        # exclude the entry threshold and staff-only areas (e.g. backroom)
        if zid == "ENTRY" or z.get("staff_only") or zid in seen:
            continue
        seen.add(zid)
        out.append({"zone_id": zid, "department": z.get("department", ""),
                    "name": z.get("name", zid)})
    return out

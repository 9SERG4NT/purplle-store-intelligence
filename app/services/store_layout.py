"""Lightweight store-layout access for the API (known stores, zone -> department).

Loads every `store_layout*.json` in the data dir and indexes them by store_id, so
the API serves multiple stores (e.g. STORE_BLR_002 + STORE_BLR_009) with each
store's own zone catalogue. Independent of the pipeline package so the API image
never needs cv2/torch.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings


@lru_cache
def _layouts() -> dict[str, dict]:
    """All store layouts, indexed by store_id. The configured primary layout is
    loaded first; any sibling `store_layout*.json` in the same dir is added too."""
    primary = Path(get_settings().store_layout_path)
    paths: list[Path] = []
    if primary.exists():
        paths.append(primary)
    for p in sorted(primary.parent.glob("store_layout*.json")):
        if p not in paths:
            paths.append(p)

    out: dict[str, dict] = {}
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = d.get("store_id")
        if sid and sid not in out:
            out[sid] = d
    if not out:
        out["STORE_BLR_002"] = {"store_id": "STORE_BLR_002", "timezone": "Asia/Kolkata", "zones": []}
    return out


def _primary_store_id() -> str:
    return next(iter(_layouts()))


def known_store_ids() -> list[str]:
    return list(_layouts().keys())


def store_timezone(store_id: str) -> str:
    lay = _layouts().get(store_id) or _layouts()[_primary_store_id()]
    return lay.get("timezone", "Asia/Kolkata")


def is_known_store(store_id: str) -> bool:
    return store_id in _layouts()


def analytics_zones(store_id: str | None = None) -> list[dict]:
    """Named zones that count for heatmap / dead-zone (exclude entry threshold and
    staff-only areas). Scoped to one store when store_id is given; otherwise the
    primary store. An unknown store yields no configured zones — observed zones
    from its events still show."""
    layouts = _layouts()
    if store_id is None:
        zones = layouts[_primary_store_id()].get("zones", [])
    elif store_id in layouts:
        zones = layouts[store_id].get("zones", [])
    else:
        zones = []

    out = []
    seen = set()
    for z in zones:
        zid = z["zone_id"]
        # exclude the entry threshold (any store's, by department) and staff-only areas
        if z.get("department") == "entry" or z.get("staff_only") or zid in seen:
            continue
        seen.add(zid)
        out.append({"zone_id": zid, "department": z.get("department", ""),
                    "name": z.get("name", zid)})
    return out

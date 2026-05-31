"""Loads store_layout.json and answers zone questions for the pipeline."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

from geometry import point_in_polygon

DEFAULT_LAYOUT = Path(__file__).resolve().parent.parent / "data" / "store_layout.json"


@dataclass
class Zone:
    zone_id: str
    name: str
    camera_id: str
    department: str
    staff_only: bool
    polygon: list[tuple[float, float]]


@dataclass
class Camera:
    camera_id: str
    source_file: str
    role: str  # entry | floor | billing | backroom
    fps_sample: int
    clip_start_local: str
    zones: list[str]
    entry_line: Optional[list[tuple[float, float]]] = None
    inside_point: Optional[tuple[float, float]] = None
    staff_region: Optional[list[tuple[float, float]]] = None
    queue_region: Optional[list[tuple[float, float]]] = None

    @property
    def clip_start(self) -> datetime:
        return datetime.fromisoformat(self.clip_start_local)


class StoreLayout:
    def __init__(self, path: Path | str = DEFAULT_LAYOUT):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.raw = data
        self.store_id: str = data["store_id"]
        self.pos_store_id: str = data.get("pos_store_id", data["store_id"])
        self.store_name: str = data.get("store_name", "")
        self.timezone: str = data.get("timezone", "Asia/Kolkata")
        self.zones: dict[str, Zone] = {
            z["zone_id"]: Zone(
                zone_id=z["zone_id"],
                name=z["name"],
                camera_id=z["camera_id"],
                department=z["department"],
                staff_only=z.get("staff_only", False),
                polygon=[tuple(p) for p in z["polygon"]],
            )
            for z in data["zones"]
        }
        self.cameras: dict[str, Camera] = {}
        self.by_source: dict[str, Camera] = {}
        for c in data["cameras"]:
            cam = Camera(
                camera_id=c["camera_id"],
                source_file=c["source_file"],
                role=c["role"],
                fps_sample=c.get("fps_sample", 5),
                clip_start_local=c["clip_start_local"],
                zones=c.get("zones", []),
                entry_line=[tuple(p) for p in c["entry_line"]] if c.get("entry_line") else None,
                inside_point=tuple(c["inside_point"]) if c.get("inside_point") else None,
                staff_region=[tuple(p) for p in c["staff_region"]] if c.get("staff_region") else None,
                queue_region=[tuple(p) for p in c["queue_region"]] if c.get("queue_region") else None,
            )
            self.cameras[cam.camera_id] = cam
            self.by_source[cam.source_file] = cam

    def zone_at(self, camera_id: str, norm_pt: tuple[float, float]) -> Optional[Zone]:
        """Which zone (if any) on this camera contains the normalised foot point."""
        for zid in self.cameras[camera_id].zones:
            zone = self.zones[zid]
            if point_in_polygon(norm_pt, zone.polygon):
                return zone
        return None


@lru_cache(maxsize=4)
def get_layout(path: str | None = None) -> StoreLayout:
    return StoreLayout(path or DEFAULT_LAYOUT)

"""Pure-geometry helpers for zone assignment and entry-line crossing.

All coordinates here are NORMALISED (x, y in [0, 1]) so the logic is
resolution-independent. Pixel -> normalised conversion happens at the edge
(detect.py) right after we read a bounding box.
"""
from __future__ import annotations

from typing import Sequence

Point = tuple[float, float]
Polygon = Sequence[Point]


def point_in_polygon(pt: Point, polygon: Polygon) -> bool:
    """Ray-casting point-in-polygon test. Robust enough for convex/concave zones."""
    x, y = pt
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _side(line_a: Point, line_b: Point, pt: Point) -> float:
    """Signed area / cross product: which side of directed line a->b is pt on.

    >0, <0 or ==0 (on the line). Sign is what matters for crossing detection.
    """
    return (line_b[0] - line_a[0]) * (pt[1] - line_a[1]) - (
        line_b[1] - line_a[1]
    ) * (pt[0] - line_a[0])


def crossing_direction(
    line_a: Point,
    line_b: Point,
    inside_point: Point,
    prev_pt: Point,
    cur_pt: Point,
) -> str | None:
    """Detect whether a track crossed the entry line between two foot positions.

    Returns "inbound" (moved to the same side as inside_point), "outbound"
    (moved away from inside), or None if the line was not crossed.
    """
    prev_side = _side(line_a, line_b, prev_pt)
    cur_side = _side(line_a, line_b, cur_pt)
    if prev_side == 0 or cur_side == 0:
        return None
    if (prev_side > 0) == (cur_side > 0):
        return None  # both points on the same side -> no crossing
    inside_side = _side(line_a, line_b, inside_point)
    moved_to_inside = (cur_side > 0) == (inside_side > 0)
    return "inbound" if moved_to_inside else "outbound"


def foot_point(xyxy: Sequence[float], frame_w: int, frame_h: int) -> Point:
    """Bottom-centre of a bounding box, normalised. The feet are the most
    reliable proxy for *where on the floor* a person is standing."""
    x1, y1, x2, y2 = xyxy
    cx = (x1 + x2) / 2.0
    fy = y2
    return (cx / frame_w, fy / frame_h)

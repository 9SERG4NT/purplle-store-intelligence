"""Zone heatmap: visit frequency + avg dwell, normalised 0-100 for grid render."""
from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.schemas import HeatmapCell, HeatmapResponse
from app.services.sessions import load_sessions
from app.services.store_layout import analytics_zones
from app.services.window import resolve_window


def compute_heatmap(db: DbSession, store_id: str, date_str: str | None = None) -> HeatmapResponse:
    start, end = resolve_window(db, store_id, date_str)
    if start is None:
        return HeatmapResponse(store_id=store_id, window_start=None, window_end=None,
                               sessions_in_window=0, data_confidence="NO_DATA", cells=[])

    customers = load_sessions(db, store_id, start, end).customers
    n_sessions = len(customers)

    visits: dict[str, int] = {}
    dwell_sum: dict[str, int] = {}
    dwell_n: dict[str, int] = {}
    for s in customers:
        seen = set(s.zones_visited)
        if s.reached_billing:
            seen.add("BILLING")
        for zid in seen:
            visits[zid] = visits.get(zid, 0) + 1
        for zid, dwell in s.zone_dwell_ms.items():
            dwell_sum[zid] = dwell_sum.get(zid, 0) + dwell
            dwell_n[zid] = dwell_n.get(zid, 0) + 1

    max_visits = max(visits.values(), default=0)
    cells = []
    for z in analytics_zones():
        zid = z["zone_id"]
        v = visits.get(zid, 0)
        avg_dwell = round(dwell_sum.get(zid, 0) / dwell_n[zid], 1) if dwell_n.get(zid) else 0.0
        score = round(100.0 * v / max_visits, 1) if max_visits else 0.0
        cells.append(HeatmapCell(zone_id=zid, department=z["department"],
                                 visits=v, avg_dwell_ms=avg_dwell, score=score))

    confidence = "LOW" if n_sessions < get_settings().low_confidence_sessions else "OK"
    return HeatmapResponse(store_id=store_id, window_start=start, window_end=end,
                           sessions_in_window=n_sessions, data_confidence=confidence, cells=cells)

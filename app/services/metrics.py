"""Real-time store metrics (North Star: conversion rate). Staff excluded."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.schemas import MetricsResponse, ZoneDwell
from app.services.sessions import WindowData, load_sessions
from app.services.window import resolve_window


def _empty(store_id: str) -> MetricsResponse:
    """Zero-traffic / unknown-day response — valid JSON, never null or a crash."""
    return MetricsResponse(
        store_id=store_id, window_start=None, window_end=None,
        unique_visitors=0, converted_visitors=0, conversion_rate=0.0, purchases=0,
        avg_dwell_by_zone=[], current_queue_depth=0, max_queue_depth=0,
        abandonment_rate=0.0, staff_excluded=0, data_confidence="NO_DATA",
    )


def compute_metrics(db: DbSession, store_id: str, date_str: str | None = None) -> MetricsResponse:
    start, end = resolve_window(db, store_id, date_str)
    if start is None:
        return _empty(store_id)
    data = load_sessions(db, store_id, start, end)
    return _from_window(store_id, start, end, data)


def _from_window(store_id: str, start: datetime, end: datetime, data: WindowData) -> MetricsResponse:
    settings = get_settings()
    customers = data.customers
    unique = len(customers)
    converted = sum(1 for s in customers if s.converted)
    conversion_rate = round(converted / unique, 4) if unique else 0.0

    # avg dwell per zone across customers who recorded a dwell there
    zone_sum: dict[str, int] = {}
    zone_n: dict[str, int] = {}
    for s in customers:
        for zid, dwell in s.zone_dwell_ms.items():
            zone_sum[zid] = zone_sum.get(zid, 0) + dwell
            zone_n[zid] = zone_n.get(zid, 0) + 1
    avg_dwell = [
        ZoneDwell(zone_id=z, avg_dwell_ms=round(zone_sum[z] / zone_n[z], 1), visits=zone_n[z])
        for z in sorted(zone_sum)
    ]

    depths = data.queue_depths
    current_qd = max(depths, key=lambda d: d[0])[1] if depths else 0
    max_qd = max((d[1] for d in depths), default=0)

    billing_customers = [s for s in customers if s.reached_billing]
    abandoned = sum(1 for s in billing_customers if s.abandoned and not s.converted)
    abandonment_rate = round(abandoned / len(billing_customers), 4) if billing_customers else 0.0

    confidence = "LOW" if unique < settings.low_confidence_sessions else "OK"

    return MetricsResponse(
        store_id=store_id, window_start=start, window_end=end,
        unique_visitors=unique, converted_visitors=converted,
        conversion_rate=conversion_rate, purchases=data.purchases,
        avg_dwell_by_zone=avg_dwell, current_queue_depth=current_qd, max_queue_depth=max_qd,
        abandonment_rate=abandonment_rate, staff_excluded=data.staff_count,
        data_confidence=confidence,
    )

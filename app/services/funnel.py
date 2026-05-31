"""Conversion funnel: Entry -> Zone Visit -> Billing Queue -> Purchase.

Session-based (unit = visitor_id) and *cumulative/nested* so the funnel is
monotonic by construction: reaching a deeper stage implies the shallower ones.
This is robust to imperfect cross-camera Re-ID (a customer seen only at billing
still counts as having browsed) — the trade-off is argued in CHOICES.md.
"""
from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from app.schemas import FunnelResponse, FunnelStage
from app.services.sessions import load_sessions
from app.services.window import resolve_window


def _dropoff(prev: int, cur: int) -> float | None:
    if prev <= 0:
        return None
    return round((1 - cur / prev) * 100, 1)


def compute_funnel(db: DbSession, store_id: str, date_str: str | None = None) -> FunnelResponse:
    start, end = resolve_window(db, store_id, date_str)
    if start is None:
        return FunnelResponse(store_id=store_id, window_start=None, window_end=None,
                              stages=[], overall_conversion_pct=0.0)

    customers = load_sessions(db, store_id, start, end).customers

    entered = len(customers)
    browsed = sum(1 for s in customers
                  if s.zones_visited or s.reached_billing or s.converted)
    billing = sum(1 for s in customers if s.reached_billing or s.converted)
    purchased = sum(1 for s in customers if s.converted)

    stages = [
        FunnelStage(stage="entered", count=entered, dropoff_pct_from_prev=None),
        FunnelStage(stage="browsed_zone", count=browsed, dropoff_pct_from_prev=_dropoff(entered, browsed)),
        FunnelStage(stage="billing_queue", count=billing, dropoff_pct_from_prev=_dropoff(browsed, billing)),
        FunnelStage(stage="purchased", count=purchased, dropoff_pct_from_prev=_dropoff(billing, purchased)),
    ]
    overall = round(purchased / entered * 100, 1) if entered else 0.0
    return FunnelResponse(store_id=store_id, window_start=start, window_end=end,
                          stages=stages, overall_conversion_pct=overall)

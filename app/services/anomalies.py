"""Operational anomaly detection: queue spike, dead zone, conversion drop.

"Now" is anchored to the most recent event for the store, so anomalies are
meaningful when replaying historical footage as well as on a live feed.
Every anomaly carries a severity and a concrete suggested_action.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.schemas import Anomaly, AnomaliesResponse
from app.services.sessions import load_sessions
from app.services.store_layout import analytics_zones, is_known_store
from app.services.window import latest_event_ts, resolve_window


def _as_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _conversion_for(db: DbSession, store_id: str, start: datetime, end: datetime) -> float | None:
    data = load_sessions(db, store_id, start, end)
    customers = data.customers
    if not customers:
        return None
    return sum(1 for s in customers if s.converted) / len(customers)


def compute_anomalies(db: DbSession, store_id: str, date_str: str | None = None) -> AnomaliesResponse:
    settings = get_settings()
    ref = latest_event_ts(db, store_id)
    start, end = resolve_window(db, store_id, date_str)
    if ref is None or start is None:
        return AnomaliesResponse(store_id=store_id, reference_time=ref, anomalies=[])

    anomalies: list[Anomaly] = []
    data = load_sessions(db, store_id, start, end)

    # --- 1. billing queue spike (current depth) ---
    if data.queue_depths:
        current_qd = max(data.queue_depths, key=lambda d: d[0])[1]
        if current_qd >= settings.queue_spike_critical:
            sev = "CRITICAL"
        elif current_qd >= settings.queue_spike_warn:
            sev = "WARN"
        else:
            sev = None
        if sev:
            anomalies.append(Anomaly(
                type="BILLING_QUEUE_SPIKE", severity=sev, zone_id="BILLING",
                value=float(current_qd), threshold=float(settings.queue_spike_warn),
                message=f"Billing queue depth is {current_qd}.",
                suggested_action="Open an additional billing counter or redirect floor staff to checkout.",
                detected_at=ref,
            ))

    # --- 2. dead zones (no customer visit in last N minutes) ---
    # Candidate zones = those observed in the window (any store/schema) plus, for
    # our configured store, its layout zones (so an unvisited zone still flags).
    dead_cutoff = ref - timedelta(minutes=settings.dead_zone_minutes)
    window_len_min = (end - start).total_seconds() / 60.0
    candidates: dict[str, datetime | None] = {
        zid: meta.get("last_visit") for zid, meta in data.zone_meta.items()
    }
    if is_known_store(store_id):
        for z in analytics_zones():
            if z["zone_id"] != "BILLING":
                candidates.setdefault(z["zone_id"], None)
    for zid, lv in candidates.items():
        if lv is None and window_len_min >= settings.dead_zone_minutes:
            anomalies.append(_dead_zone(zid, ref, None, settings))
        elif lv is not None and lv < dead_cutoff:
            anomalies.append(_dead_zone(zid, ref, lv, settings))

    # --- 3. conversion drop vs trailing 7-day average ---
    today_conv = _conversion_for(db, store_id, start, end)
    baseline = _baseline_conversion(db, store_id, start)
    if today_conv is not None and baseline is not None and baseline > 0:
        if today_conv <= baseline * (1 - settings.conversion_drop_pct):
            drop = round((1 - today_conv / baseline) * 100, 1)
            sev = "CRITICAL" if today_conv <= baseline * 0.5 else "WARN"
            anomalies.append(Anomaly(
                type="CONVERSION_DROP", severity=sev, value=round(today_conv, 4),
                threshold=round(baseline, 4),
                message=f"Conversion {today_conv:.1%} is {drop}% below the 7-day average {baseline:.1%}.",
                suggested_action="Check staffing, queue length and product availability on the floor.",
                detected_at=ref,
            ))

    return AnomaliesResponse(store_id=store_id, reference_time=ref, anomalies=anomalies)


def _dead_zone(zid: str, ref: datetime, last: datetime | None, settings) -> Anomaly:
    if last is None:
        msg = f"Zone {zid} has had no customer visits in this window."
    else:
        mins = round((ref - last).total_seconds() / 60.0)
        msg = f"Zone {zid} has had no customer visits for {mins} min."
    return Anomaly(
        type="DEAD_ZONE", severity="WARN", zone_id=zid,
        value=None, threshold=float(settings.dead_zone_minutes),
        message=msg,
        suggested_action=f"Send a staff member to refresh the {zid} display or check for an obstruction.",
        detected_at=ref,
    )


def _baseline_conversion(db: DbSession, store_id: str, window_start: datetime) -> float | None:
    """Mean conversion over the up-to-7 store-days before the current window."""
    vals = []
    for d in range(1, 8):
        s = window_start - timedelta(days=d)
        e = s + timedelta(days=1)
        c = _conversion_for(db, store_id, s, e)
        if c is not None:
            vals.append(c)
    return sum(vals) / len(vals) if vals else None

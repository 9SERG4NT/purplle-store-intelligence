"""Pydantic schemas — the wire contract. Mirrors pipeline/emit.py exactly."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
}


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0


class EventIn(BaseModel):
    """One detection event. Used to validate each item in an ingest batch.

    Note: low-confidence events are NOT rejected (the brief: "do not suppress
    low-conf events"); confidence is stored and surfaced, never used to drop.
    """
    event_id: str = Field(min_length=1, max_length=64)
    store_id: str = Field(min_length=1, max_length=64)
    camera_id: str = Field(default="", max_length=64)
    visitor_id: str = Field(min_length=1, max_length=64)
    event_type: str
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = Field(default=0, ge=0)
    is_staff: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in EVENT_TYPES:
            raise ValueError(f"event_type must be one of {sorted(EVENT_TYPES)}")
        return v


class IngestRequest(BaseModel):
    # raw dicts so a single malformed event doesn't reject the whole batch
    events: list[dict[str, Any]]


class RejectedEvent(BaseModel):
    index: int
    event_id: Optional[str] = None
    error: str


class IngestResponse(BaseModel):
    received: int
    accepted: int
    duplicates: int
    rejected: int
    rejected_details: list[RejectedEvent] = []


# ---- read-model responses (also documents the API in OpenAPI) -------------

class ZoneDwell(BaseModel):
    zone_id: str
    avg_dwell_ms: float
    visits: int


class MetricsResponse(BaseModel):
    store_id: str
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    unique_visitors: int
    converted_visitors: int
    conversion_rate: float
    purchases: int
    avg_dwell_by_zone: list[ZoneDwell]
    current_queue_depth: int
    max_queue_depth: int
    abandonment_rate: float
    staff_excluded: int
    data_confidence: str


class FunnelStage(BaseModel):
    stage: str
    count: int
    dropoff_pct_from_prev: Optional[float]


class FunnelResponse(BaseModel):
    store_id: str
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    stages: list[FunnelStage]
    overall_conversion_pct: float


class HeatmapCell(BaseModel):
    zone_id: str
    department: str
    visits: int
    avg_dwell_ms: float
    score: float  # 0..100


class HeatmapResponse(BaseModel):
    store_id: str
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    sessions_in_window: int
    data_confidence: str
    cells: list[HeatmapCell]


class Anomaly(BaseModel):
    type: str
    severity: str  # INFO | WARN | CRITICAL
    zone_id: Optional[str] = None
    value: Optional[float] = None
    threshold: Optional[float] = None
    message: str
    suggested_action: str
    detected_at: datetime


class AnomaliesResponse(BaseModel):
    store_id: str
    reference_time: Optional[datetime]
    anomalies: list[Anomaly]


class StoreHealth(BaseModel):
    store_id: str
    last_event_ts: Optional[datetime]
    last_ingest_ts: Optional[datetime]
    lag_seconds: Optional[float]
    stale_feed: bool
    event_count: int


class HealthResponse(BaseModel):
    status: str  # ok | degraded
    version: str
    db_connected: bool
    server_time: datetime
    stores: list[StoreHealth]

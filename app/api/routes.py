"""HTTP routes. Thin controllers; all logic lives in app/services/*."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Event
from app.schemas import (
    AnomaliesResponse, FunnelResponse, HealthResponse, HeatmapResponse,
    IngestRequest, IngestResponse, MetricsResponse,
)
from app.services import anomalies, funnel, health, heatmap, ingestion, metrics
from app.services.store_layout import is_known_store

router = APIRouter()


def _ensure_store(db: Session, store_id: str) -> None:
    """Known stores always answer (zeros if empty). Unknown + no data => 404."""
    if is_known_store(store_id):
        return
    exists = db.execute(
        select(Event.event_id).where(Event.store_id == store_id).limit(1)
    ).first()
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "store_not_found", "store_id": store_id},
        )


@router.post("/events/ingest", response_model=IngestResponse, tags=["ingest"])
def ingest(payload: IngestRequest, request: Request, db: Session = Depends(get_db)) -> IngestResponse:
    request.state.event_count = len(payload.events)
    if len(payload.events) > ingestion.MAX_BATCH:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "batch_too_large",
                    "max": ingestion.MAX_BATCH, "received": len(payload.events)},
        )
    return ingestion.ingest_events(db, payload.events)


@router.get("/stores/{store_id}/metrics", response_model=MetricsResponse, tags=["analytics"])
def get_metrics(store_id: str, db: Session = Depends(get_db),
                date: str | None = Query(None, description="YYYY-MM-DD; default = latest data day")):
    _ensure_store(db, store_id)
    return metrics.compute_metrics(db, store_id, date)


@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse, tags=["analytics"])
def get_funnel(store_id: str, db: Session = Depends(get_db), date: str | None = Query(None)):
    _ensure_store(db, store_id)
    return funnel.compute_funnel(db, store_id, date)


@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse, tags=["analytics"])
def get_heatmap(store_id: str, db: Session = Depends(get_db), date: str | None = Query(None)):
    _ensure_store(db, store_id)
    return heatmap.compute_heatmap(db, store_id, date)


@router.get("/stores/{store_id}/anomalies", response_model=AnomaliesResponse, tags=["analytics"])
def get_anomalies(store_id: str, db: Session = Depends(get_db), date: str | None = Query(None)):
    _ensure_store(db, store_id)
    return anomalies.compute_anomalies(db, store_id, date)


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def get_health(db: Session = Depends(get_db)) -> HealthResponse:
    return health.compute_health(db)

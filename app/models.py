"""SQLAlchemy ORM models: ingested events and POS transactions."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, Index, Integer, String, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Event(Base):
    __tablename__ = "events"

    # event_id is the idempotency key: re-ingesting the same id is a no-op.
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(64), index=True)
    camera_id: Mapped[str] = mapped_column(String(64))
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    zone_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    dwell_ms: Mapped[int] = mapped_column(Integer, default=0)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    queue_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sku_zone: Mapped[str | None] = mapped_column(String(48), nullable=True)
    session_seq: Mapped[int] = mapped_column(Integer, default=0)
    # --- official multi-source schema extras (all nullable) ---
    zone_name: Mapped[str | None] = mapped_column(String(96), nullable=True)
    zone_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_revenue_zone: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age_bucket: Mapped[str | None] = mapped_column(String(16), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    group_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wait_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    abandoned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_events_store_ts", "store_id", "ts"),
        Index("ix_events_store_visitor", "store_id", "visitor_id"),
        Index("ix_events_store_type", "store_id", "event_type"),
    )


class PosTransaction(Base):
    __tablename__ = "pos_transactions"

    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    basket_value_inr: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (Index("ix_pos_store_ts", "store_id", "ts"),)

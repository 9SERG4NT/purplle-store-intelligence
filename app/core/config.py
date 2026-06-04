"""Application settings (12-factor: everything overridable via env)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres in docker-compose; override with sqlite for local/tests.
    database_url: str = "sqlite:///./store.db"

    store_layout_path: str = str(REPO_ROOT / "data" / "store_layout.json")
    pos_csv_path: str = str(REPO_ROOT / "data" / "pos_transactions.csv")

    # Business-logic windows / thresholds (documented in DESIGN.md).
    conversion_window_minutes: int = 5      # billing presence before a txn => converted
    stale_feed_minutes: int = 10            # /health STALE_FEED if no ingest in this long
    dead_zone_minutes: int = 30             # /anomalies DEAD_ZONE
    queue_spike_warn: int = 4               # queue_depth >= => WARN
    queue_spike_critical: int = 7           # queue_depth >= => CRITICAL
    conversion_drop_pct: float = 0.30       # today vs 7-day avg drop to flag
    low_confidence_sessions: int = 20       # heatmap data_confidence threshold

    log_level: str = "INFO"
    app_version: str = "1.0.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()

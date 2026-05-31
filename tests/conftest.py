"""Shared pytest fixtures.

Runs the whole API against an in-memory SQLite database so tests need no
Postgres. Tables are recreated before every test for isolation. A `seed` helper
posts events through the real ingest path so tests exercise the same code a
client would.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

# Must be set BEFORE importing the app (engine is built at import time).
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
# Point POS at an empty temp dir so lifespan loads 0 rows (tests insert their own).
_TMP = tempfile.mkdtemp(prefix="si_tests_")
os.environ["POS_CSV_PATH"] = os.path.join(_TMP, "pos_transactions.csv")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import PosTransaction  # noqa: E402

STORE = "STORE_BLR_002"
BASE_TS = datetime(2026, 4, 10, 14, 50, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def _client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(_client) -> TestClient:
    # fresh schema for every test
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield _client


@pytest.fixture
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---- builders ---------------------------------------------------------------

def make_event(visitor_id, event_type, *, offset_s=0, zone=None, dwell_ms=0,
               is_staff=False, confidence=0.9, queue_depth=None, seq=1,
               camera_id="CAM_ENTRY_01", store_id=STORE, event_id=None, ts=None):
    when = ts or (BASE_TS + timedelta(seconds=offset_s))
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "store_id": store_id, "camera_id": camera_id, "visitor_id": visitor_id,
        "event_type": event_type, "timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone, "dwell_ms": dwell_ms, "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {"queue_depth": queue_depth, "sku_zone": zone, "session_seq": seq},
    }


@pytest.fixture
def mk():
    """The event builder, as a fixture (tests/ isn't an importable package)."""
    return make_event


@pytest.fixture
def seed(client):
    def _seed(events: list[dict]):
        r = client.post("/events/ingest", json={"events": events})
        assert r.status_code == 200, r.text
        return r.json()
    return _seed


@pytest.fixture
def add_pos():
    """Insert POS transactions directly (UTC ISO 'Z' strings)."""
    def _add(rows: list[tuple[str, str, float]]):
        with SessionLocal() as s:
            for tid, ts, amount in rows:
                s.merge(PosTransaction(
                    transaction_id=tid, store_id=STORE,
                    ts=datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
                    basket_value_inr=amount))
            s.commit()
    return _add

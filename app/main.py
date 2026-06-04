"""FastAPI entrypoint: lifespan, middleware, graceful-degradation handlers."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.api.routes import router
from app.core.config import get_settings
from app.core.database import DatabaseUnavailable, SessionLocal, init_db
from app.core.logging import RequestLogMiddleware, configure_logging

logger = logging.getLogger("store_intel")


def _startup_db(retries: int = 10, delay: float = 2.0) -> bool:
    """Wait for Postgres (it may boot slower than the API), then create tables."""
    for attempt in range(1, retries + 1):
        try:
            init_db()
            return True
        except OperationalError as exc:
            logger.warning("db not ready (attempt %s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    logger.error("database unreachable after %s attempts; starting in degraded mode", retries)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    if _startup_db():
        try:
            from app.services.pos_loader import load_pos_csv
            primary = Path(settings.pos_csv_path)
            demo = primary.parent / "demo_pos.csv"
            pos_path = primary if primary.exists() else demo  # fall back to demo POS
            with SessionLocal() as db:
                n = load_pos_csv(db, pos_path)
            logger.info("startup complete", extra={"pos_loaded": n, "pos_source": str(pos_path)})
        except Exception as exc:  # POS is optional; never block startup on it
            logger.warning("POS load skipped: %s", exc)
    yield


app = FastAPI(
    title="Apex Retail — Store Intelligence API",
    version=get_settings().app_version,
    description="Real-time offline-store analytics from CCTV-derived events.",
    lifespan=lifespan,
)

app.add_middleware(RequestLogMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.exception_handler(DatabaseUnavailable)
async def _db_unavailable(request: Request, exc: DatabaseUnavailable):
    return JSONResponse(
        status_code=503,
        content={"error": "database_unavailable",
                 "detail": "The analytics datastore is temporarily unreachable.",
                 "trace_id": getattr(request.state, "trace_id", None)},
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    # Never leak stack traces to clients (Part C). Full detail goes to the logs.
    logger.exception("unhandled error", extra={"trace_id": getattr(request.state, "trace_id", None)})
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error",
                 "trace_id": getattr(request.state, "trace_id", None)},
    )


app.include_router(router)


@app.get("/", tags=["ops"])
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard/")


_dash = Path(__file__).resolve().parent.parent / "dashboard"
if _dash.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_dash), html=True), name="dashboard")

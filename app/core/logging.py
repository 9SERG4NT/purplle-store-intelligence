"""Structured JSON logging + per-request observability middleware.

Every request emits one JSON log line with: trace_id, store_id, endpoint,
latency_ms, event_count (ingest), status_code — exactly the fields Part C asks
for. trace_id is also returned as the X-Trace-Id response header so a log line
can be tied back to a specific call.
"""
from __future__ import annotations

import logging
import time
import uuid

from pythonjsonlogger import jsonlogger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import get_settings

logger = logging.getLogger("store_intel")


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler()
    handler.setFormatter(jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
    ))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    # uvicorn's own access log would duplicate ours; quiet it.
    logging.getLogger("uvicorn.access").handlers = []


def _store_id_from_path(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "stores":
        return parts[1]
    return None


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        request.state.trace_id = trace_id
        request.state.event_count = None
        start = time.perf_counter()
        status_code = 500
        response = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request",
                extra={
                    "trace_id": trace_id,
                    "store_id": _store_id_from_path(request.url.path),
                    "endpoint": request.url.path,
                    "method": request.method,
                    "latency_ms": latency_ms,
                    "event_count": getattr(request.state, "event_count", None),
                    "status_code": status_code,
                },
            )

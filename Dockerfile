# Multi-stage build (skill: multi-stage-dockerfile):
#   builder -> compiles/installs deps into a venv
#   runtime -> minimal slim image, non-root, healthcheck, only what's needed to run
# The detection pipeline's heavy CV deps (torch/ultralytics/opencv) are deliberately
# NOT in this image — it ships only the lean Intelligence API.

# ---------- builder ----------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---------- runtime ----------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/venv/bin:$PATH"
# non-root user (security skill guidance)
RUN useradd --create-home --uid 10001 appuser
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app
COPY dashboard ./dashboard
COPY scripts ./scripts
COPY data ./data
COPY pyproject.toml ./
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# Liveness: the API answers /health even when the DB is degraded.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

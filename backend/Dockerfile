# Multi-stage build.
#
# The builder stage installs dependencies into a virtual environment, and the
# runtime stage copies only that environment plus the application code. The
# final image therefore contains no compilers, no pip cache and no build tools,
# which keeps it small and reduces the attack surface.

# --- Stage 1: build the virtual environment ---------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Dependencies are copied and installed before the source code so this layer is
# cached and only rebuilt when the requirements change.
COPY backend/requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# --- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

# curl is needed by the container healthcheck.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIDEO_RAG_ENVIRONMENT=production \
    VIDEO_RAG_DATA_DIR=/app/data \
    VIDEO_RAG_FRONTEND_DIR=/app/frontend \
    VIDEO_RAG_API_HOST=0.0.0.0 \
    VIDEO_RAG_API_PORT=8000

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY backend/src ./src
COPY backend/pyproject.toml ./
# The web page ships inside the same image, served by the API.
COPY frontend ./frontend

# Run as an unprivileged user. The data directory is owned by that user so the
# SQLite registry and local vector file remain writable.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# The health endpoint is unauthenticated on purpose so orchestrators can probe
# it without holding an API key.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# One worker per container keeps the in-process job store and rate limiter
# correct. Scale by running more containers behind a load balancer, and move
# the vector store to Qdrant so state is shared.
CMD ["uvicorn", "video_rag.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

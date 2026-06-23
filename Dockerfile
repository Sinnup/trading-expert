# =============================================================
# Trading Expert — Dockerfile
# =============================================================
# Multi-stage build using uv for fast, reproducible installs.
#
# Build:
#   docker build -t trading-expert .
#
# Run (with docker compose):
#   docker compose up
# =============================================================

FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (layer caching)
COPY pyproject.toml .python-version ./

# Install deps into venv
RUN uv sync --frozen --no-dev --no-editable

# --- Runtime stage ---
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy venv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY config/ ./config/
COPY trading_expert/ ./trading_expert/

# Copy data directory for SQLite
RUN mkdir -p /app/data/logs

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Default command (overridden by docker-compose for workers)
CMD ["python", "-m", "trading_expert.cli", "watch"]

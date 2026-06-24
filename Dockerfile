# =============================================================
# Trading Expert — Dockerfile
# =============================================================

FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files and source
COPY pyproject.toml uv.lock .python-version ./
COPY trading_expert/ ./trading_expert/
COPY config/ ./config/

# Install dependencies (non-editable, production only)
# Note: no --frozen because uv.lock has local paths that differ in Docker
RUN uv sync --no-dev

# Create data directory for SQLite
RUN mkdir -p /app/data/logs

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Default command (overridden by docker-compose for workers)
CMD ["python", "-m", "trading_expert.cli", "watch"]

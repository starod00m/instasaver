# syntax=docker/dockerfile:1.7

# ---- Builder stage: install dependencies into a venv with uv ----
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# uv pinned for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

# Materialize dependencies into /app/.venv. --no-dev skips dev deps.
RUN uv sync --frozen --no-dev --no-install-project

# ---- Runtime stage ----
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root user (UID 10001 per ops runbook).
RUN groupadd -r appuser && useradd -r -g appuser -u 10001 appuser \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the pre-built virtualenv from the builder stage.
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Copy application source.
COPY --chown=appuser:appuser bot /app/bot

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "-m", "bot"]

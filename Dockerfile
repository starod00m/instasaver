# Use Python 3.13 slim image
FROM python:3.13-slim

# Metadata
LABEL maintainer="instasaver" \
      description="Instagram Reels downloader bot for Telegram" \
      version="0.1.0"

# Set working directory
WORKDIR /app

# Install system dependencies for yt-dlp
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install uv with fixed version for reproducibility
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Copy application code
COPY bot.py ./

# Create non-root user and set up permissions
RUN useradd -m -u 1000 -s /bin/bash botuser && \
    mkdir -p temp && \
    chown -R botuser:botuser /app

# Switch to non-root user
USER botuser

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run the bot directly using the virtual environment
CMD ["python", "bot.py"]

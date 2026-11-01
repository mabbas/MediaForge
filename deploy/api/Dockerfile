# ==============================================================================
# MediaForge / GrabItDown — FastAPI Application Dockerfile
# Production-aware development container running as non-root with FFmpeg.
# ==============================================================================

FROM python:3.11-slim

LABEL maintainer="MediaForge Engineering" \
      description="MediaForge / GrabItDown FastAPI Backend Service"

# 1. Install system runtime dependencies (FFmpeg, FFprobe, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 2. Create non-root system user and group
RUN groupadd -g 1000 mediaforge && \
    useradd -u 1000 -g mediaforge -m -s /bin/bash mediaforge

# 3. Setup application directory structure with appropriate permissions
WORKDIR /app

RUN mkdir -p /app/data /app/downloads /app/logs && \
    chown -R mediaforge:mediaforge /app

# 4. Copy build manifests and install Python dependencies
COPY --chown=mediaforge:mediaforge pyproject.toml README.md ./

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# 5. Copy application source code and configuration
COPY --chown=mediaforge:mediaforge api ./api
COPY --chown=mediaforge:mediaforge src ./src
COPY --chown=mediaforge:mediaforge config ./config
COPY --chown=mediaforge:mediaforge alembic.ini ./alembic.ini

# 6. Environment configuration
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/home/mediaforge/.local/bin:${PATH}" \
    GID_API_HOST=0.0.0.0 \
    GID_API_PORT=8000 \
    GID_DOWNLOAD__OUTPUT_DIRECTORY=/app/downloads

# 7. Switch to non-root user
USER mediaforge

# 8. Expose internal application port
EXPOSE 8000

# 9. Container health check using existing API health endpoint
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/v1/health || exit 1

# 10. Start production ASGI server (without dev reload)
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

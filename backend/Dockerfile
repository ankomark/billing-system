# Image shared by the web, worker, beat and flower services in docker-compose.yml.
# Each service overrides the command; CMD below is only the default.
#
# Build context is this directory (compose uses `build: .` and lives here).

FROM python:3.12-slim

# - PYTHONDONTWRITEBYTECODE: no .pyc clutter in the layer
# - PYTHONUNBUFFERED: logs reach docker logs immediately instead of being buffered
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is used by the healthcheck below. psycopg[binary] and cryptography ship
# manylinux wheels, so no compiler toolchain is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first so pip layer is cached across source-only changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /health/ checks the DB and cache (see billing.views.health_check).
#
# ${PORT} because a PaaS picks the port and passes it in — Railway defaults to
# 8080. Hardcoded, this check curls a port nothing listens on and the container
# reports unhealthy forever while serving traffic perfectly well. Falls back to
# 8000 for compose, which sets no PORT.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/health/ || exit 1

# Default only — compose overrides this per service. The compose `web` command
# runs collectstatic first so WhiteNoise has files to serve.
CMD ["gunicorn", "backend.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]

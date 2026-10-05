# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install uv (fast, reproducible installs from the lockfile).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

# Apply OS security updates and install libmagic (needed by python-magic at
# runtime for MIME sniffing). Keep the image lean and pin nothing to :latest.
RUN apt-get update \
    && apt-get -y upgrade \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Copy application source.
COPY app ./app
COPY workers ./workers
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# Entrypoint applies `alembic upgrade head` before serving. Made executable
# before the ownership change, since a non-root user cannot chmod a file it
# does not own.
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]

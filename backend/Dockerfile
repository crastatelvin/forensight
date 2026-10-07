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
# Operational scripts, so the container can provision into its own volumes rather
# than the key having to be copied in from a host. `provision_signer.py` in
# particular writes straight to the mounted signing-key volume, which keeps the only
# copy of a signing key inside that volume instead of spreading it across the host.
COPY scripts ./scripts

# Entrypoint applies `alembic upgrade head` before serving. Made executable
# before the ownership change, since a non-root user cannot chmod a file it
# does not own.
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app

# The signing-key volume is created by Docker owned by root, so an unprivileged
# container cannot write the key into it. Made writable here, at image build time,
# rather than by loosening the entrypoint: this grants write access only to the one
# directory that holds a key, and only inside this image. The key is written 0600 by
# scripts/provision_signer.py, so a second user inside the container still cannot
# read it.
RUN mkdir -p /var/lib/forensight/signing && chown appuser:appuser /var/lib/forensight/signing

USER appuser

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]

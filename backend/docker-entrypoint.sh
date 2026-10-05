#!/bin/sh
# Container entrypoint: bring the schema up to date, then serve.
#
# Migrations run here rather than being left to the operator because an API that
# boots against an empty schema fails every DB route with a 500 that looks like
# an application bug rather than a missing migration.
set -e

# Dependencies live in /app/.venv (installed by `uv sync` at build time); it is
# not on PATH, so every command is run through `uv run`.
UV="uv run --no-sync"

echo "==> applying migrations"
$UV alembic upgrade head

echo "==> starting gunicorn"
# Rate limiting keys on client IP. Behind a reverse proxy that is the proxy's
# address, so every caller would share one bucket — uvicorn's proxy-headers
# makes the worker trust X-Forwarded-For. FORWARDED_ALLOW_IPS defaults to *
# because in this topology the only thing reaching the port is the proxy; set it
# explicitly if the API is ever exposed directly.
exec $UV gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers "${WEB_CONCURRENCY:-2}" \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
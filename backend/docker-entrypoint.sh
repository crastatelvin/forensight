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
#
# --timeout is raised from gunicorn's 30s default, and that default breaks the
# agent outright. `GET /agent/runs/{id}/stream` is an SSE response whose whole
# purpose is to stay open while the model works, so any run longer than 30s got
# its worker SIGKILLed mid-stream — and gunicorn reports that as "Perhaps out of
# memory?", which sends you looking at memory when memory is fine (the container's
# cgroup recorded zero OOM kills). Found by running the stack; no test could see
# it, because every backend test drives the graph in-process and never goes
# through gunicorn.
#
# The cost, stated rather than hidden: a genuinely wedged request now holds its
# worker for up to this long instead of 30s. That is the trade for supporting
# unbounded streaming in the same process. It is adjustable — a deployment that
# prefers a tight reap can lower it, or serve SSE from a separate service.
exec $UV gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers "${WEB_CONCURRENCY:-2}" \
    --timeout "${WEB_TIMEOUT:-900}" \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
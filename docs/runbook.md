# Runbook — ForenSight

Single-host Docker Compose. Commands assume the repo root.

## Start

```bash
cp .env.example .env
# Set SECRET_KEY, and COOKIE_SECURE=false for plain-http localhost.
docker compose --profile core up -d --build
curl -fsS http://localhost:8000/readyz
```

The API container runs `alembic upgrade head` before serving, so the schema is
always current. Never skip it: an API against an empty schema fails every DB
route with a 500 that looks like an application bug.

## First admin

There are no default credentials. Create the first one explicitly:

```bash
docker compose exec \
  -e SEED_ADMIN_EMAIL=you@example.com \
  -e SEED_ADMIN_PASSWORD='a-long-passphrase' \
  api python -m app.seed
```

Re-running is a no-op if the account already exists.

## Verify health

```bash
curl -s localhost:8000/healthz      # process up; no dependency checks
curl -s localhost:8000/readyz       # 200 only when PG + Redis + MinIO reachable
curl -s localhost:8000/capabilities # which analyzers this host can run
curl -s localhost:8000/metrics | head
```

## Common failures

### `/readyz` returns 503

The response names the failing dependency. In order of likelihood:

```bash
docker compose --profile core ps                       # which service is unhealthy
docker compose logs postgres --tail 50
docker compose logs minio --tail 50
```

### Postgres restarts, `relation "vector" does not exist`

The init scripts only run on an **empty** data directory. After changing
`infra/postgres/init/`, force a clean bootstrap:

```bash
docker compose --profile core down -v && docker compose --profile core up -d
```

`down -v` destroys all data, evidence included. Back up first (see below).

### API exits with `alembic: not found`

The entrypoint runs commands through `uv run`, because dependencies live in
`/app/.venv`, which is not on `PATH`.

### Every DB route 500s, but `/healthz` is 200

The schema is behind the code. Check the migration log at container start; if
the container is running an image built before a migration existed, rebuild:

```bash
docker compose --profile core build --no-cache api && docker compose --profile core up -d
```

### 429 on login

`RATE_LIMIT_AUTH_MAX` (default 5 per 60s) is per client IP. Behind a proxy, an
incorrect `FORWARDED_ALLOW_IPS` makes every client share one bucket. Confirm the
proxy's address is in the allowlist.

### `403 CSRF token missing or invalid`

Cookie-authenticated mutation without the `X-CSRF-Token` header echoing the
`csrf_token` cookie. `/auth/login` and `/auth/refresh` are exempt — they
establish the session, so no cookie exists yet. Bearer-token requests are also
exempt.

### Observability profile will not start

Loki needs `infra/observability/loki.yaml` mounted; without it the container has
no config to read. Prometheus scraping a 404 means the API image predates the
`/metrics` endpoint.

## Migrations

```bash
docker compose exec api alembic current        # applied revision
docker compose exec api alembic history        # full chain
docker compose exec api alembic upgrade head   # apply (also happens at boot)
docker compose exec api alembic downgrade -1   # step back one
```

CI applies `upgrade head -> downgrade base -> upgrade head` against real
Postgres, so reversibility is checked on every run.

## Backups

Phase 10 formalizes this; until then, capture both stores — they are
meaningless apart, since evidence rows point at object keys.

```bash
# Postgres
docker compose exec -T postgres pg_dump -U forensight forensight \
  | gzip > forensight-$(date +%F).sql.gz

# Evidence objects
docker run --rm -v forensight_miniodata:/data -v "$PWD":/backup \
  alpine tar czf /backup/minio-$(date +%F).tar.gz -C /data .
```

Restoring is untested and belongs in Phase 10 (P10.B2). Verify evidence hashes
against `evidence.sha256` after any restore.

## Scale-out notes

- Multiple API replicas need `FORWARDED_ALLOW_IPS` set to the real proxy, and
  rate limiting stays correct because it is Redis-backed rather than per-process.
- `WEB_CONCURRENCY` sets gunicorn workers per container.
- Audit appends serialize on a Postgres advisory lock, so concurrent writers
  across replicas do not fork the chain.

## Degraded mode

| Component down | Behaviour |
|---|---|
| Redis | Rate limiting falls back to a per-process counter; notifications dropped. Requests keep working. |
| MinIO | Uploads/downloads fail; `/readyz` reports `minio: false`. |
| Postgres | Everything DB-backed fails. Nothing degrades silently. |
| GPU absent | `/capabilities` reports GPU modules unavailable with a reason. Never faked. |
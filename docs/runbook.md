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
## Analysis pipeline

Submit analysis of one evidence item with one module:

```bash
curl -X POST localhost:8000/cases/$CASE/analyses \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"module":"byte-entropy","evidence_id":"'$EVID'"}'
```

Then either wait for a worker (the `core` profile runs one) or run it inline:

```bash
curl -X POST localhost:8000/analyses/jobs/$JOB/run -H "Authorization: Bearer $TOKEN"
curl localhost:8000/cases/$CASE/results -H "Authorization: Bearer $TOKEN"
```

### Job status

| Status | Meaning |
|---|---|
| `queued` | Accepted, waiting for a worker |
| `running` | A worker is executing it |
| `done` | Result recorded |
| `failed` | Analysis or infrastructure error; see `error` on the job |
| `unsupported` | This host cannot run the module; see `unsupported_reason` |

`unsupported` is deliberately not `failed` — nothing went wrong, and reporting
it as a failure would suggest a bug and invite a pointless retry.

### Workers

```bash
docker compose logs worker --tail 50      # CPU worker
docker compose --profile gpu-workers up -d worker-gpu
```

`worker` runs in the `core` profile; `worker-gpu` is separate because the nvidia
device reservation makes compose refuse to start on a host without the driver,
which would take the whole stack down on a laptop. Give a GPU worker
`GPU_AVAILABLE=true` only when it genuinely has one.

### Idempotency

The key is `sha256(evidence_hash + evidence_id + module + params + model_version)`
and is UNIQUE in the database. Consequences:

- Re-submitting identical work returns the existing job; it never recomputes.
- Changing params, or bumping a module's `model_version`, produces a new job.
- Identical bytes uploaded into a *different* case get their own job. The
  content hash alone would reproduce a score, but a job is also a record inside
  a case, so sharing job rows across cases would misreport where the work
  happened and leak cross-case structure.

To invalidate cached results, bump the module's `version`. That is the intended
lever and nothing else should need it.

### Adding an analyzer

1. Write it in `backend/app/analysis/modules/<name>.py`: a `ModuleSpec` plus a
   function `run(data: bytes, params: dict) -> dict` returning at least `score`.
2. Register it in `registry.register_all()`.
3. Nothing else changes — API, worker, caching, and audit already work.

A module must be deterministic: no randomness, no wall-clock time, no
unversioned library that could change between runs. A test enforces this by AST
scan. Also fill in `threshold`, `tier`, and `version` — the result payload
cannot be built without them, and a score is never stored without the bar it was
judged against.

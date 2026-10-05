# ForenSight

Production-grade forensic investigation platform: evidence vault with WORM
storage, hash-chained audit, chain-of-custody, and a real AI agent (tools +
memory).

> Build status: **Phase 3 — case management API & workflow** complete
> (see `CHECKLIST.md`). Phases 4-10 — analysis workers, the LangGraph agent,
> analyzers, the Next.js frontend, signed reports, and ops — are not built yet.
> `agent/`, `workers/`, and `frontend/` are placeholders.

## Monorepo layout

| Path | Purpose |
|---|---|
| `backend/` | FastAPI API gateway (auth, REST, WebSocket, OpenAPI) |
| `agent/` | LangGraph autonomous agent service (tools, memory, HITL) |
| `workers/` | Async analysis workers (Celery + real ML analyzers) |
| `frontend/` | Next.js (App Router, TypeScript, Tailwind, shadcn/ui) |
| `infra/` | Docker Compose, deployment, observability configs |
| `docs/` | Architecture, runbooks, threat model, admin guide |
| `tests/` | Cross-cutting / E2E test assets |

## Planning documents

- `DESIGN_PLAN.md` — authoritative architecture & decisions.
- `IMPLEMENTATION_PLAN.md` — phased roadmap (P0–P10) with test cases.
- `CHECKLIST.md` — checkbox progress tracker (source of truth for "done").
- `FUTURE_IMPROVEMENTS.md` — candidate features (not in core path).

## Quickstart (development)

Prerequisites: Docker + Docker Compose, `uv` (Python), Node.js.

```bash
# 1. Copy env template and set a real SECRET_KEY (app refuses to boot without it)
cp .env.example .env
#    SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
#    ...and write it into .env, plus COOKIE_SECURE=false for plain-http localhost.

# 2. Bring up core services (Postgres+PostGIS+pgvector, Redis, MinIO, API).
#    The API container applies `alembic upgrade head` before serving.
docker compose --profile core up -d --build

# 3. Check readiness (200 only when DB/Redis/MinIO reachable)
curl -fsS http://localhost:8000/readyz

# 4. Create the first admin (no default credentials exist)
docker compose exec -e SEED_ADMIN_EMAIL=you@example.com \
                    -e SEED_ADMIN_PASSWORD='a-long-passphrase' \
    api python -m app.seed
```

### Compose profiles

| Profile | Brings up | Status |
|---|---|---|
| `core` | postgres, redis, minio, api | works |
| `gpu-workers` | GPU-enabled analysis workers | Phase 4 — image not built yet |
| `observability` | Prometheus, Grafana, Loki | Prometheus scrapes `/metrics` |
| `android-dev` | CPU-only API + Postgres + small Ollama; GPU modules degrade cleanly | |

## Backend development (without Docker)

```bash
cd backend
uv sync                 # installs pinned deps into .venv (Python 3.12)
uv run pytest           # run tests (SQLite; no services required)
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy app         # type-check (strict)
uv run uvicorn app.main:app --reload
```

Tests default to in-memory SQLite. Two suites need live services and are skipped
unless explicitly enabled:

```bash
FORENSIGHT_PG_LIVE=1 FORENSIGHT_MINIO_LIVE=1 uv run pytest
```

## Authorization model

Two independent layers, both required:

- **RBAC** (`app/auth/rbac.py`) — which *roles* may call a mutating route.
  Reviewer never appears in a mutating role set.
- **Case scope** (`app/domain/access.py`) — which *cases* a user may see.
  Officers and experts see only cases they own or are assigned to; admin and
  reviewer see all (reviewer remains read-only). Knowing an id is not
  authorization, so a case the caller cannot see is reported as `404`, not
  `403` — a 403 would confirm the id exists.

## Honesty clause

Tier-A/B analyzer outputs are **investigative leads / visual similarity**, not
courtroom individualization. This disclaimer is baked into the UI and reports.

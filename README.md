# ForenSight

Production-grade full-stack forensic investigation platform with a real autonomous AI agent (tools + memory).

> Build status: **Phase 0 — Foundation & scaffolding** (see `CHECKLIST.md`).

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

# 2. Bring up core services (Postgres+PostGIS+pgvector, Redis, MinIO, API)
docker compose --profile core up -d

# 3. Check readiness (200 only when DB/Redis/MinIO reachable)
curl -fsS http://localhost:8000/readyz
```

### Compose profiles

| Profile | Brings up |
|---|---|
| `core` | postgres, redis, minio, api |
| `gpu-workers` | GPU-enabled analysis workers |
| `observability` | Prometheus, Grafana, Loki |
| `android-dev` | CPU-only API + Postgres + small Ollama; GPU modules degrade cleanly |

## Backend development (without Docker)

```bash
cd backend
uv sync                 # installs pinned deps into .venv (Python 3.12)
uv run pytest           # run tests
uv run ruff check .     # lint
uv run mypy app         # type-check
uv run uvicorn app.main:app --reload
```

## Honesty clause

Tier-A/B analyzer outputs are **investigative leads / visual similarity**, not
courtroom individualization. This disclaimer is baked into the UI and reports.

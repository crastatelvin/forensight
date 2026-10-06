# ForenSight

Production-grade forensic investigation platform: evidence vault with WORM
storage, hash-chained audit, chain-of-custody, and a real AI agent (tools +
memory).

> Build status: **Phase 5 complete; Phase 6 begun** (see `CHECKLIST.md`).
> The agent core is real: it plans, calls typed and audited tools, refuses to
> assert anything it cannot cite to an analyzer result, and pauses for human
> approval before changing state. A live model (`qwen2.5:3b`) drives the tool loop
> end to end and produces a sourced report — see `CHECKLIST.md` T5.10.
>
> **Phase 6 complete.** Six analyzers ship. `byte-entropy` is a deterministic
> reference module. The
> image modules are real forensic techniques — `image-ela` scores compression
> anomalies against the image's own error baseline, `image-exif` scores
> metadata-integrity indicators, and `image-prnu` correlates sensor fingerprints to
> say whether two frames came from the same camera. `object-detect` runs pinned
> YOLOv4 weights on
> `onnxruntime`, and `face-compare` runs pinned YuNet + SFace for one-to-one face
> verification; both are digest-verified before load and the agent invokes them
> through `run_analysis`. **Two capabilities are honestly missing**: the object
> detector cannot find weapons (COCO has no firearm class, so its watchlist is
> `knife`/`scissors` and a nil result says nothing about guns), and the face
> threshold is the model vendor's published operating point, not one calibrated on
> this deployment. The Next.js frontend (`frontend/` is a placeholder), signed
> reports, and ops hardening are also still ahead.

## Monorepo layout

| Path | Purpose |
|---|---|
| `backend/` | FastAPI API gateway (auth, REST, WebSocket, OpenAPI) |
| `backend/app/agent/` | Autonomous agent: graph, tools, provenance guard, memory, HITL |
| `backend/workers/` | Analysis workers (Dramatiq; four analyzers registered, see `docs/runbook.md`) |
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
| `core` | postgres, redis, minio, ollama, api, CPU analysis worker | works |
| `gpu-workers` | GPU analysis worker (`worker-gpu`) | needs the nvidia container runtime |
| `observability` | Prometheus, Grafana, Loki | Prometheus scrapes `/metrics` |
| `android-dev` | CPU-only API + Postgres + small Ollama; GPU modules degrade cleanly | |

## The agent

The graph is `Intake → Planner → Memory → Reflector ⇄ ToolExec → HITL → Report`,
over one case, with an append-only checkpoint after every step.

Two properties are enforced in code rather than in the prompt:

- **Provenance.** Every claim in a report is resolved against a stored
  `analysis_results` row. Unsourced, hallucinated, cross-case, or paraphrased
  citations are rejected. A model that cannot source a claim produces a recorded
  `refusal`, never an unsourced sentence.
- **Human approval.** Every state-changing tool parks its arguments for explicit
  approval. Approve and reject are both audited, and an approval cannot be
  replayed against different arguments.

Prompt injection is handled in three layers — neutralise, wrap as labelled
untrusted data, and enforce case scope in SQL — so a jailbroken model is still
bounded by the case it was started for. See [`docs/runbook.md`](docs/runbook.md).

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

## Analyzers

| Module | Tier | Score means |
|---|---|---|
| `byte-entropy` | B | Shannon entropy, bits/byte |
| `image-ela` | A | fraction of pixels with anomalously high error levels |
| `image-exif` | B | fraction of metadata indicators that fired |
| `image-prnu` | B | correlation against a reference sensor fingerprint |
| `object-detect` | A | highest confidence among watchlist detections |
| `face-compare` | B | cosine similarity between two faces' embeddings |

All run on CPU; no GPU module has been written, so nothing depends on one.

```bash
backend/scripts/fetch_models.sh                    # pinned weights, digest-verified (~300 MB, not committed)
cd backend && python scripts/provision_models.py .models   # upload them to the MinIO `models` bucket
```

Provisioning is a deliberate step, not automatic: a deployment that skips it gets
every model-backed job failing with "pinned weights are not provisioned", and
`/capabilities` reporting those analyzers unavailable with that reason. Nothing
falls back to an unpinned model.

Every result carries its threshold, model version, tier and disclaimer. A
technique that does not apply to an input (ELA on a PNG, an image with no face in
it) is recorded as `not_applicable` with no result row, so "could not measure" is
never rendered as "measured zero". Each module documents its own limitations in the
result payload — that `object-detect` cannot detect firearms because COCO has no
firearm class, that `face-compare` verifies (1:1) but never identifies (1:N)
against a threshold not calibrated here, and that `image-prnu` reports whether two
frames share a **camera**, never whether they share a person.

## Honesty clause

Tier-A/B analyzer outputs are **investigative leads / visual similarity**, not
courtroom individualization. This disclaimer is baked into the UI and reports.

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
| Model weights not provisioned | Model-backed jobs fail with "pinned weights are not provisioned"; `/capabilities` marks them unavailable. Never falls back to an unpinned model. |

A job also ends in a terminal state other than `done`/`failed` when the technique
does not apply or the host cannot run the module. These are distinct from failure
because nothing went wrong:

| Status | Meaning | Result row? |
|---|---|---|
| `unsupported` | host lacks a capability this module needs (e.g. no GPU) | no |
| `not_applicable` | module ran; technique undefined for this input (ELA on a PNG, no face found) | no |
| `failed` | the module raised | no |

The `unsupported_reason` field carries the explanation in all three cases.

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

### Provisioning model weights

Modules with pinned weights load their artifacts from the MinIO `models` bucket
at run time, verified against the SHA-256 in the module's `ModuleSpec`. Nothing
creates that bucket or uploads anything automatically — that is a deliberate
provisioning step, and a deployment that skips it gets every model-backed job
failing with a clear message rather than a silent fallback.

```bash
backend/scripts/fetch_models.sh          # fetch + digest-verify (~300 MB)
cd backend && python scripts/provision_models.py .models
```

`provision_models.py` is idempotent: an artifact already present with the right
digest is left alone, so re-running after a version bump uploads only what
changed.

`GET /capabilities` is built from the analysis registry rather than a
hand-maintained list, so it always describes the analyzers this build actually
ships. An analyzer with a `model_key` and no provisioned artifact is reported
unavailable with the reason "pinned weights are not provisioned", which is a
different problem from "no GPU on this host" and needs a different fix.

### Job status

| Status | Meaning |
|---|---|
| `queued` | Accepted, waiting for a worker |
| `running` | A worker is executing it |
| `done` | Result recorded |
| `failed` | Analysis or infrastructure error; see `error` on the job |
| `unsupported` | This host cannot run the module; see `unsupported_reason` |
| `not_applicable` | The module ran; the technique does not apply to this input |

Neither `unsupported` nor `not_applicable` is `failed` — nothing went wrong, and
reporting either as a failure would suggest a bug and invite a pointless retry.
Neither writes a result row, so "could not measure" is never confused with
"measured zero". `unsupported_reason` carries the explanation.

### From the agent

The agent's `run_analysis` tool runs the job **inline when no queue broker is
configured**, so a run can finish with a citable result in one pass. That is not a
simulation: it calls the same `execute_job` the Dramatiq worker does, with the
same pinned-model verification and the same refusal statuses. Only the transport
differs.

With a broker configured the job is queued instead, and the tool returns
`result_available: false` — the agent is told plainly that there is nothing to
cite yet rather than being sent to look for a result it cannot see.

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

### Analyzers

Three modules ship today. `byte-entropy` is the Phase 4 reference module;
`image-ela` and `image-exif` are the first real forensic techniques (P6.B3).

| Module | Tier | Score means | Needs |
|---|---|---|---|
| `byte-entropy` | B | Shannon entropy in bits/byte | nothing |
| `image-ela` | A | fraction of pixels with anomalously high error level | JPEG input |
| `image-exif` | B | fraction of metadata indicators that fired | JPEG input |
| `image-prnu` | B | correlation against a reference sensor fingerprint | 2 images from one camera |
| `object-detect` | A | highest confidence among detections of the watchlist classes | pinned weights |
| `face-compare` | B | cosine similarity between two faces' embeddings | pinned weights + 2 images |

```bash
curl -X POST localhost:8000/cases/$CASE/analyses \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"module":"image-ela","evidence_id":"'$EVID'"}'
```

### Model-backed analyzers

`object-detect` and `face-compare` load pinned weights from the MinIO `models`
bucket under `models/<module>/<version>/<file>`. The executor verifies the
SHA-256 against the value in the module's `ModuleSpec` **before** load and passes
the verified bytes to the module; a mismatch fails the job rather than falling
back to a different artifact.

To get the weights on a developer machine or in CI:

```bash
backend/scripts/fetch_models.sh          # fetch + digest-verify (~300 MB)
export FORENSIGHT_YOLO_WEIGHTS=.models/yolov4.onnx
pytest tests/test_object_detect.py       # otherwise these tests skip
pytest tests/test_face_compare.py        # defaults to .models, no env needed
```

The weights are 257 MB and are never committed. `FORENSIGHT_YOLO_WEIGHTS` also
bypasses MinIO entirely for local testing; in a deployed stack leave it unset and
the artifact comes from the bucket — see "Provisioning model weights" above.

**Weapon detection does not work.** YOLOv4 is trained on COCO's 80 classes and
COCO contains no firearm, rifle, or pistol class, so `object-detect` cannot find
the objects this platform most often needs to find. The default watchlist is
`knife` and `scissors` — the only edged objects COCO knows — and `0.0` means "no
knife and no scissors", not "no weapon". Closing this needs a model trained on
weapons, not a threshold change.

### Face comparison

`face-compare` does **verification** (1:1), never identification (1:N): it
compares two images you name and cannot search a face against a database. That
distinction is also a legal boundary — one-to-many biometric search is restricted
or prohibited in several jurisdictions.

It needs a second image, named by the job's params:

```bash
curl -X POST localhost:8000/cases/$CASE/analyses \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"module":"face-compare","evidence_id":"'$EVID'",
       "params":{"reference_evidence_id":"'"$REF_EVID"'"}}'
```

The reference must belong to the same case. It is resolved case-scoped in the
executor, so a job cannot name evidence from a case the caller cannot see, and
both images are hash-verified before anything is compared.

Two things to know before trusting a number:

- **The 0.593 threshold is SFace's published operating point, not one calibrated
  here.** A defensible threshold needs a validation set of thousands of labelled
  pairs from your own population. Every result says
  `calibrated_on_this_deployment: false`.
- **No liveness or presentation-attack check.** A photograph of a photograph will
  produce a confident result. Treat any match as a lead for a trained examiner.

Models are Apache-2.0 (YuNet detects, SFace embeds). SFace is the weaker of the
two commonly-used face stacks; see `CHECKLIST.md` P6.B1.

### Sensor fingerprinting (PRNU)

Every camera stamps a faint per-pixel pattern into every frame it takes.
`image-prnu` correlates a query frame against a reference fingerprint to answer
**"did these two frames come from the same camera body?"**

It does **not** answer "are these the same person". Two different people shot on
one camera correlate as a match; one person shot on two cameras does not. Treat
the result as evidence about an image's origin, never about its subject.

```bash
curl -X POST localhost:8000/cases/$CASE/analyses \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"module":"image-prnu","evidence_id":"'$EVID'",
       "params":{"reference_evidence_id":"'"$REF"'"}}'
```

Three things before you rely on it:

- **The reference must be from the same camera as the query.** A fingerprint built
  from other shots of the same *scene* matches nothing. Supplying several reference
  frames helps a lot: one frame is a residual, not a fingerprint, and the payload
  reports `reference_is_single_frame` when only one was given.
- **The threshold is a screening value from this repo's synthetic fixtures**, not
  a calibrated operating point. Correlation thresholds depend entirely on the
  camera population and imaging pipeline.
- **In-camera processing erodes fingerprints.** Demosaicing, sharpening and noise
  reduction all attack them, and phone cameras apply all three aggressively. A low
  correlation is not proof a different camera was used.

### Adding an analyzer

1. Write it in `backend/app/analysis/modules/<name>.py`: a `ModuleSpec` plus a
   function `run(data: bytes, params: dict) -> dict` returning at least `score`.
2. Register it in `registry.register_all()`.
3. Nothing else changes — API, worker, caching, and audit already work.

A module must be deterministic: no randomness, no wall-clock time, no
unversioned library that could change between runs. A test enforces this by AST
scan (it allows numpy, which is deterministic; it bans its random entry points).
Also fill in `threshold`, `tier`, and `version` — the result payload cannot be
built without them, and a score is never stored without the bar it was judged
against.

Two rules the image modules exist to demonstrate:

- **Put everything that can change the number in `version`.** `image-ela` encodes
  `libjpeg-turbo` because the JPEG encoder's output is baked into the score; a
  codec upgrade would otherwise change every result under an unchanged label.
  `version` feeds the idempotency key, so bumping it forces a recompute.
- **When a technique does not apply, raise `AnalysisNotApplicable`** — do not
  return 0.0. ELA on a PNG and face matching with no face present are undefined,
  not negative. The job lands in the `not_applicable` terminal state and writes no
  result row, so a report can never present "could not measure" as "measured
  nothing found".

  The converse also holds, and it is easy to get backwards: for `object-detect`, a
  0.0 *is* a real measurement. The model ran on valid input and looked. Judge each
  technique on whether the question was well-posed, not on whether the number is
  zero.
- **A module that declares `model_key` must accept `model_bytes`** and score only
  what it is handed. Fetching weights yourself means the pinned digest was checked
  on an artifact you then ignored. A second artifact goes in `spec.aux_models` and
  arrives as `aux_models`; fetch it as `<model_key>#<name>`.
- **A module needing a second image takes `reference_bytes`**, resolved from
  `params["reference_evidence_id"]`. The executor enforces the case scope, so the
  module does not re-implement it.

## Agent

The agent runs a fixed graph over one case: Intake → Planner → Memory →
Reflector ⇄ ToolExec → HITL → Report. It never widens its own scope — the case
id comes from the run, not from the model's arguments.

```bash
curl localhost:8000/agent/tools -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/agent/cases/$CASE/runs \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"objective":"identify the entry point"}'

curl localhost:8000/agent/runs/$RUN            # status, report or refusal
curl localhost:8000/agent/runs/$RUN/tool-calls # audit: arg hash + result hash
curl localhost:8000/agent/runs/$RUN/checkpoints
curl -N localhost:8000/agent/runs/$RUN/stream # SSE
```

#### Testing the agent against a real model

Every other agent test drives a scripted model, which proves the graph *handles*
tool calls but not that a model *makes* them. That gap hid two real bugs, so it
has its own test:

```bash
FORENSIGHT_LLM_LIVE=1 pytest tests/test_agent_live.py
```

It needs a reachable OpenAI-compatible provider and live Postgres. Defaults to
`http://localhost:11434/v1` with `qwen2.5:3b`; set `LLM_BASE_URL`, `LLM_MODEL`
and `LLM_API_KEY` to point at a cloud provider instead.

The model must be told the tools exist. Two format requirements, both of which
fail silently rather than raising, are handled in `app/agent/tools.py`
(`to_model_tools`) and `app/agent/llm.py`:

- tools go on the wire as `{"type": "function", "function": {...}}`; a flat
  catalog is accepted and ignored
- a tool result goes back as a `role: "tool"` turn carrying `tool_call_id`, not
  as prose in a user turn

### Human approval

A run that calls a state-changing tool stops with `status=awaiting_approval`
and returns the frozen action. Approve or reject it explicitly:

```bash
curl -X POST localhost:8000/agent/runs/$RUN/approve \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"action_id":"'"$ACTION"'"}'

curl -X POST localhost:8000/agent/runs/$RUN/reject \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"action_id":"'"$ACTION"'","reason":"not supported by the evidence"}'
```

Both decisions are audited. An `action_id` that does not match the parked action
is refused — you cannot approve something other than what you were shown.

### Reports and refusals

A run ends as either:

- `completed` with a report where **every claim carries its citations** — result
  id, analyzer, score, threshold, model version, and tier, repeated per claim so
  a reader seeing one line still sees the limitation.
- `declined` with a `refusal` explaining what was missing.

`declined` is a first-class outcome, not a failure. A model that cannot source a
claim produces a recorded refusal, never an unsourced sentence.

### Providers

Switch with configuration only:

```bash
LLM_PROVIDER=ollama LLM_MODEL=qwen2.5:7b LLM_BASE_URL=http://ollama:11434/v1
LLM_PROVIDER=cloud  LLM_MODEL=gpt-4o-mini    LLM_API_KEY=...
```

Leaving `LLM_PROVIDER` empty returns **503 with a reason** rather than silently
defaulting — case data must never be sent somewhere the operator did not choose.

Embeddings must return exactly `EMBED_DIM` (1024) values; that is the pgvector
column width, and it is the width `bge-m3` actually returns via Ollama. A
different embedding model needs both a new `EMBED_DIM` and a migration — it is
not a config tweak. Width is validated on write, so a mismatch fails at the
insert with a clear message.

Models must be pulled before use:

```bash
docker exec forensight-ollama-1 ollama pull bge-m3
docker exec forensight-ollama-1 ollama pull qwen2.5:7b
```

### Prompt injection

Evidence is attacker-controlled, so it is never treated as instructions:

1. Injection shapes in evidence are neutralised (not rejected — the tampering
   attempt is itself a finding) and the finding is recorded.
2. Content reaches the model inside a labelled `<<<UNTRUSTED_EVIDENCE>>>`
   block, capped in size so it cannot evict the system prompt.
3. Tool scope is enforced in SQL, not in the prompt. A fully jailbroken model is
   still bounded by the case it was started for.

An operator's *objective* is trusted input, so an injection shape there is
refused with **422** rather than sanitised.

### Adding a tool

1. Add a Pydantic args model and an `async def` in `app/agent/builtin.py`,
   decorated with `@define_tool`. Set `state_changing=True` if it writes.
2. Add it to the tuple in `builtin.register_all()`.

It is then typed, audited, case-scoped, approval-gated if it writes, and
published in the catalogue. Tools must never take a case id as an argument —
that is how scope escapes.

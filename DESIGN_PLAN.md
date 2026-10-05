# ForenSight — Redesign Plan

> Production-grade full-stack forensic investigation platform with a real autonomous AI agent (tools + memory). This document is the authoritative redesign plan. It records the full architecture, tech stack, module map, multi-dimensional design, migration path, and the locked-in decisions.

---

## Decisions (locked)

1. **Module scope:** Approved. Cut Tier-C (DNA, toxicology, pollen, insect, odor, explosive chemistry, arson accelerant chemistry) as AI analyzers; rebuild them as lab-result attachment workflows.
2. **LLM:** Use a cloud LLM for now via a pluggable adapter (LiteLLM). Keep Ollama (local) as a first-class option to run on-device later. No code change required to switch.
3. **Vector store:** pgvector — one PostgreSQL instance for relational + vector + PostGIS. Simplest ops, production-proven. Migrate to Qdrant only if scale demands.
4. **Repo:** New private repo `forensight`.
5. **Auth:** Built-in JWT + RBAC (fastapi-users + Argon2id), with Keycloak/OIDC as a documented optional upgrade.

---

## The reality check (this determines the whole redesign)

The old app claimed 32 forensic modules. Most were `random.uniform(...)`. The uncomfortable fact is that you cannot build real, court-admissible DNA, toxicology, pollen, insect, odor, or bite-mark analysis from an uploaded image in a web app — those require physical lab instruments (mass spectrometers, sequencers, microscopy) and accredited chain-of-custody. No open-source model changes that. Keeping them as "AI modules" is exactly the simulation we want to eliminate.

So "everything real" forces a decision: drop the fake disciplines and build fewer modules that are genuinely real, backed by actual open-source models with published benchmarks. Below is exactly which survive, which get repositioned, and which get cut. That honesty is the single most important part of this redesign.

---

## 1. Requirement understanding (what we are actually building)

Convert a fake-AI Flask demo into a production-grade full-stack forensic investigation platform whose centerpiece is a real autonomous AI agent (not a chatbot) with:

- **Tools** — the agent can invoke real analysis capabilities, query the case DB, read/write evidence, generate reports, search.
- **Memory** — short-term (conversation), long-term (semantic/episodic), and case-scoped memory.
- **Planning/reasoning** — multi-step, tool-using, with human-in-the-loop for sensitive actions.
- Everything real, everything open-source, production hardened, multi-dimensional (feature, logic, security, UI, UX, data, ops).

The agent becomes the investigator's copilot: it plans an investigation, runs real analyzers as tools, records findings with provenance, maintains chain-of-custody, and produces defensible reports.

---

## 2. Which forensic modules are actually real (the honest module map)

**Tier A — Genuinely real with open-source models (KEEP, build properly):**

| Module | Real open-source basis |
|---|---|
| Face comparison / recognition | InsightFace (ArcFace) or `face_recognition`/dlib — real embeddings + cosine distance, with calibrated thresholds |
| Object / weapon detection | YOLOv8/YOLO11 (Ultralytics), real COCO/custom weights |
| Fingerprint matching | NIST NBIS (MINDTCT + BOZORTH3) — the actual government-grade minutiae matcher, open source |
| Deepfake / face-manipulation detection | Real models (e.g. Xception/EfficientNet trained on FaceForensics++), ONNX export |
| Speaker / voice comparison | SpeechBrain ECAPA-TDNN speaker embeddings — real x-vector similarity |
| Document / handwriting similarity | Real CV features (SIFT/ORB) + Siamese net; honest "similarity score" not "identity" |
| Image forensics (tampering/ELA/metadata) | Error-Level Analysis, EXIF/`exiftool`, noise/PRNU — real signal forensics |
| Digital text authorship / phishing | Real transformer classifiers (fine-tuned DistilBERT), URL/heuristic analysis |
| Geospatial correlation | Real geopy/Shapely/PostGIS distance + clustering on actual coordinates |
| Audio event (gunshot) classification | Real audio CNN (e.g. YAMNet/PANNs) on log-mel spectrograms |
| Ballistics / toolmark / tiremark / shoeprint image similarity | Real deep-metric image similarity (honest "visual similarity", not individualization) |

**Tier B — Reposition honestly (KEEP but rename):** these become "visual similarity / triage assist," never "match = identity." Bloodstain, fiber, glass, paint, soil, hair-image, iris (iris can be real with OpenCV+Daugman but needs proper NIR data).

**Tier C — CUT (impossible to do for real from a web upload):** DNA, toxicology, pollen, insect (forensic entomology), odor profile, explosive chemical ID, arson accelerant chemistry. Keep them only as case-metadata/report-attachment workflows ("upload the accredited lab's PDF result"), not as AI analyzers. This is the difference between real and theater.

The platform is designed so modules are plugins — new Tier-A analyzers can be added over time without touching core.

---

## 3. Target architecture (full-stack, production)

```
                          +--------------------------------------+
                          |  Frontend: Next.js (React, TypeScript)|
                          |  App Router, TanStack Query, Tailwind |
                          |  shadcn/ui, streaming agent UI (SSE/WS)|
                          +---------------+----------------------+
                                          | HTTPS / WSS
                          +---------------v----------------------+
                          |  API Gateway: FastAPI (Python 3.12)  |
                          |  - Auth (OAuth2/JWT + RBAC)          |
                          |  - REST + WebSocket (agent streaming)|
                          |  - OpenAPI, Pydantic v2 validation   |
                          +---+---------------+--------------+----+
                              |               |              |
              +---------------v---+   +-------v-------+  +---v-------------+
              | Agent Service     |   | Analysis      |  | Core Services   |
              | (LangGraph)       |   | Workers       |  | cases, users,   |
              | - planner/executor|   | (Celery/      |  | evidence, audit |
              | - tool registry   |   |  RQ + GPU)    |  | reports         |
              | - memory manager  |   | real ML tools |  |                 |
              | - HITL checkpoints|   | (Tier-A)      |  |                 |
              +----+--------+-----+   +-------+-------+  +--------+--------+
                   |        |                 |                  |
      +------------v+ +-----v------+  +--------v-------+  +-------v---------+
      | LLM Layer    | | Vector DB  |  | Object Storage |  | PostgreSQL 16   |
      | Ollama/vLLM  | | pgvector / |  | MinIO (S3)     |  | + PostGIS       |
      | (local) or   | | Qdrant     |  | evidence blobs |  | cases/audit/    |
      | API adapter  | | (memory)   |  | WORM bucket    |  | chain-of-custody|
      +--------------+ +------------+  +----------------+  +-----------------+
                   |
              +----v----------------------------------+
              | Message bus: Redis (queues + pub/sub) |
              +---------------------------------------+

  Cross-cutting: OpenTelemetry + Prometheus + Grafana + Loki (obs),
  Keycloak (optional SSO), Vault/SOPS (secrets), Docker Compose / K8s.
```

**Why this shape:** the LLM agent must never block the web request, and heavy ML must never run inside the API process. Agent = orchestrator; analyzers = async GPU workers behind a queue; everything is auditable.

---

## 4. Full tech stack (all open-source, production-grade)

**Frontend**
- Next.js 14/15 (App Router) + React + TypeScript
- Tailwind CSS + shadcn/ui (Radix) for accessible components
- TanStack Query (server state), Zustand (light client state)
- Streaming agent output via WebSocket / SSE; `react-markdown` for agent messages
- Recharts or visx for dashboards; MapLibre GL (open-source, no Google) for geospatial

**Backend / API**
- FastAPI + Uvicorn/Gunicorn, Pydantic v2
- SQLAlchemy 2.0 (async) + Alembic migrations
- Authlib / fastapi-users for auth; python-jose for JWT

**Agent core**
- A stateful graph agent: planner -> tool executor -> reflector, with checkpointing and human-in-the-loop interrupts.

  *Implemented as a plain state machine rather than LangGraph — a deliberate deviation, recorded rather than quietly dropped:*
  - Interrupts must land in the audit trail (approve **and** reject). LangGraph interrupts, but not that, so an audited layer would sit on top regardless.
  - The Postgres checkpointer is ours either way; `langgraph-checkpoint-postgres` would be an alternative implementation of a table we already write.
  - What the framework would add is free-form graph composition, which we do not need until a second graph exists.

  What the state machine buys that a framework would not: the whole agent runs against a scripted fake, so provenance, case scoping, human approval and injection defence are tested with **no network and no model**. That is where the risk actually is.
- Tool layer: typed Python tool functions with strict Pydantic schemas; each tool logged to the audit trail.
- LLM abstraction: a `ChatModel` protocol with an OpenAI-compatible HTTP adapter, so you can swap Ollama (local, `qwen2.5`/`mistral`) <-> vLLM <-> external APIs without code changes. Provider is chosen by `LLM_PROVIDER` alone. Default cloud now; local later for chain-of-custody.

  *Implemented without LiteLLM — a second deliberate deviation:* every target provider (OpenAI, Azure, Groq, Together, and Ollama itself) speaks `/v1`, and `httpx` already ships. LiteLLM's value is multi-provider routing and cost accounting, which we do not use at one provider. It remains the upgrade path once there are several providers with real failover needs — it slots in as a second implementation behind the same protocol, with no caller changes.

**Memory**
- Short-term: Postgres checkpointer per conversation/case thread (append-only `agent_checkpoints`; the latest row for a run is the resumable state).
- Long-term semantic + episodic: **pgvector** `vector(1024)` with an HNSW cosine index (simplest, one DB). Embeddings via `bge-m3` served by Ollama. Scope is enforced in the SQL predicate — case **and** actor — never by filtering after the fact.
- Structured "case memory": normal Postgres tables (findings, entities, timeline) the agent reads/writes as tools — this is more reliable and auditable than dumping everything in a vector store.

**Real ML analysis workers**
- Celery (or RQ/Dramatiq) + Redis broker; GPU pool workers
- PyTorch, ONNX Runtime (for portable/CPU inference), Ultralytics YOLO, InsightFace, SpeechBrain, OpenCV, librosa
- NIST NBIS for fingerprints; `exiftool`, ELA for image forensics
- Model registry on MinIO; pinned model versions + checksums (reproducibility = admissibility)

**Data**
- PostgreSQL 16 + **PostGIS** (geospatial) as system of record
- **MinIO** (S3-compatible) for evidence blobs, with object-lock/WORM bucket for tamper-evidence
- Redis for queues, cache, pub/sub, rate limiting

**Reports**
- WeasyPrint or ReportLab for PDF; signed PDFs (PAdES) + SHA-256 manifest for integrity

**Security / ops**
- Keycloak (optional) for SSO/OIDC; or built-in JWT+RBAC
- HashiCorp Vault or SOPS+age for secrets (no `secret_key='your-secret-key-here'` ever again)
- OpenTelemetry -> Prometheus + Grafana + Loki (metrics/logs/traces)
- Trivy + Bandit + `pip-audit` + Semgrep in CI; Ruff + mypy; ESLint/Prettier
- Docker Compose for single-host; optional Helm/K8s for scale
- GitHub Actions CI

**Android/proot dev mode**
- A trimmed Compose profile: FastAPI + Postgres + Ollama (small quantized model, CPU) + a few CPU-only ONNX analyzers. GPU-heavy modules disabled with a clear capability flag. This is for demo/dev only, not production forensic use.

---

## 5. The AI Agent design (the heart of the redesign)

**Not a chatbot — a graph-based investigative agent.** LangGraph state machine:

```
[Intake] -> [Planner] -> [Tool Executor] <=> [Memory R/W]
                ^            |
           [Reflector] <-----+
                |
        [HITL Checkpoint] -> [Report Composer] -> [Done]
```

**Tools the agent can call (each is a real, audited function):**
- `run_analysis(module, evidence_ids, params)` -> enqueues a real Tier-A analyzer, returns job id, polls result
- `query_cases(filters)` / `get_case(id)` / `update_case_finding(...)`
- `search_evidence(case_id, query)` — semantic + metadata search over evidence
- `recall_memory(query, scope)` / `write_memory(fact, scope)`
- `generate_report(case_id, template)`
- `web_search`/`kb_search` (optional, over your own forensic knowledge base — restricted, no arbitrary internet in air-gapped mode)
- `compute_geo(...)`, `extract_metadata(...)`

**Memory model (three layers):**
1. **Working memory** — current thread state (LangGraph checkpoint, Postgres).
2. **Episodic memory** — past investigative steps/decisions, embedded in pgvector, scoped by case + user + org.
3. **Semantic/knowledge memory** — forensic procedures, module capabilities, precedents; RAG source.
Plus **structured case memory** (findings/entities/timeline tables) — the source of truth the agent must cite.

**Safety in the agent (critical for forensics):**
- **Human-in-the-loop interrupts** before any state change (creating findings, closing cases, deleting evidence).
- Every tool call, prompt, and model version logged to an **immutable audit trail** (append-only, hash-chained) — this is your chain-of-custody for AI actions.
- The agent must **cite provenance** for every claim (which evidence, which analyzer, which score, which model version). No unsourced assertions.
- Confidence calibration + explicit "insufficient evidence" outputs. No fabricated certainty.
- Prompt-injection defense: evidence text and file contents are treated as untrusted data, never as instructions.

---

## 6. Multi-dimensional plan

### Feature dimension
- Real Tier-A analysis modules as plugins; Tier-C reworked as lab-result attachment workflows.
- Case lifecycle: intake -> evidence upload (hashed on ingest) -> agent-assisted investigation -> findings -> review -> signed report -> archive.
- Role-based dashboards (Admin, Investigator/Officer, Forensic Expert, Reviewer/Read-only).
- Agent console with streaming reasoning, tool-call timeline, and HITL approve/reject.
- Evidence vault with chain-of-custody log, integrity verification, and access audit.
- Report builder (PDF, signed, with model/version provenance appendix).
- Full-text + semantic search across cases and evidence.
- Notifications, assignment, SLA/status tracking, CSV/JSON/ZIP export.

### Logic dimension
- Async everywhere: API never runs ML; agent orchestrates, workers compute.
- Idempotent analysis jobs keyed by (evidence hash + module + params + model version) -> same input = same result = reproducible = admissible. Cache by that key.
- Deterministic thresholds per module, documented and calibrated; scores always accompanied by the decision threshold and model version.
- Saga pattern for multi-step case updates; outbox pattern for reliable events.
- Clear separation: pure analysis functions <-> orchestration <-> persistence.

### Security dimension (fixing every old flaw + more)
- Passwords: Argon2id hashing (the `forensic_lab.db` already used scrypt — we go stronger). No plaintext, ever.
- Secrets from Vault/env, never in source; rotated `SECRET_KEY`.
- `debug=False` in prod; no debugger on network interfaces.
- File uploads: `secure_filename`, MIME sniffing, size limits, AV scan (ClamAV) on ingest, stored in MinIO by content-hash — kills path traversal and overwrite.
- Downloads: never join user paths; serve by object id with authz checks — kills the old `download_report` traversal.
- RBAC enforced centrally (dependency/middleware), not copy-pasted per route.
- Per-case access control (resource-level authorization) layered on top of RBAC:
  ADMIN read/write all; OFFICER read/write own or assigned cases; EXPERT
  read/write assigned cases; REVIEWER read-only across all cases. A read-scope
  miss returns 404 (non-disclosure of out-of-scope cases); a write attempt on a
  readable-but-not-writable case returns 403. Enforced centrally in
  `app/domain/access.py`, not per route. Search results and evidence/finding/
  entity/timeline access are scoped the same way. Case assignment validates
  that the assignee user exists.
- CSRF protection, secure/HttpOnly/SameSite cookies, TLS everywhere, HSTS.
- Rate limiting + input validation (Pydantic) on all endpoints; agent tool inputs schema-validated.
- Audit log is append-only + hash-chained; evidence bucket is WORM/object-locked.
- SAST/dep-scan/container-scan in CI (Bandit, Semgrep, pip-audit, Trivy).
- Data protection: encryption at rest (disk/MinIO SSE), PII minimization, configurable retention/purge.
- Prompt-injection & tool-abuse guards in the agent; egress control in air-gapped mode.

### UI dimension
- Next.js + shadcn/ui, accessible (WCAG 2.1 AA): keyboard nav, ARIA, focus management, contrast — the old inline neon theme fails contrast.
- Shared design system + component library (no 42 duplicated full-page templates).
- Dark/light themes via CSS variables; responsive; skeleton/loading states.
- Agent console: streaming tokens, collapsible tool-call cards, evidence citations as clickable chips, HITL action buttons.
- Evidence viewer: image/audio/video/text with metadata sidebar, side-by-side compare, ELA overlay for image forensics.
- Dashboards with real charts (Recharts) and a MapLibre map for geospatial.

### UX dimension
- Guided case workflow with a progress rail; the agent suggests next steps but the human decides.
- Explainability-first: every score shows what it means, the threshold, model version, and limitations ("visual similarity, not identity").
- Optimistic UI + clear error/empty states; no silent failures.
- Long jobs: async with progress + notification, never a frozen page.
- Trust & safety UX: confidence badges, "why this result" popovers, and honest disclaimers on non-individualizing modules.
- Audit-friendly: one click to see the full chain-of-custody and agent action timeline for any finding.

### Data dimension
- Normalized schema: users, roles, cases, evidence, evidence_versions, analysis_jobs, analysis_results, findings, entities, timeline_events, agent_threads, agent_messages, tool_calls, audit_log, reports.
- Evidence integrity: SHA-256 on ingest, stored in WORM MinIO, hash re-verified on access.
- pgvector for embeddings; PostGIS for coordinates.
- Alembic migrations; seed script (no committed DB files — the old repo committed two).
- Backups: pg_dump + MinIO replication; point-in-time recovery.

### Ops / deployment dimension
- Docker Compose (prod single-host) with profiles: `core`, `gpu-workers`, `observability`, `android-dev`.
- CI/CD (GitHub Actions): lint -> type-check -> test -> SAST -> build -> scan -> push.
- OpenTelemetry traces spanning API -> agent -> worker; Prometheus/Grafana dashboards; Loki logs; Sentry for errors.
- Health/readiness probes; graceful shutdown; horizontal scaling of API and workers.
- Model management: versioned, checksummed models in MinIO; capability flags so missing-GPU environments degrade cleanly.

### Testing / quality dimension
- pytest (unit + integration), test DB via testcontainers; Playwright for E2E UI.
- Golden tests for analyzers (known input -> known score) — proves "no simulation."
- Contract tests for agent tools; eval harness for agent behavior (does it cite sources, respect HITL).
- Load tests (Locust) for the job queue.

---

## 7. Migration path (old -> new, incremental)

1. **Foundation:** monorepo (`/frontend`, `/backend`, `/agent`, `/workers`, `/infra`), Docker Compose, Postgres+MinIO+Redis+Ollama, auth+RBAC, Argon2, migrations, CI with security scans.
2. **Core domain:** cases/evidence/users/audit + evidence vault with hashing + chain-of-custody.
3. **First 3 real analyzers** as queue workers (face via InsightFace, object via YOLO, image-forensics via ELA/exif) — proves the "real, reproducible" contract with golden tests.
4. **Agent v1:** LangGraph with tool registry, LiteLLM->cloud LLM (Ollama-ready), Postgres checkpointer, HITL, audit logging, provenance citations.
5. **Memory:** pgvector episodic/semantic + structured case memory tools.
6. **Frontend:** Next.js app — auth, dashboards, evidence viewer, streaming agent console.
7. **Reports:** signed PDFs with provenance appendix; exports.
8. **Expand analyzers** (fingerprint NBIS, speaker SpeechBrain, deepfake, gunshot audio) as plugins.
9. **Harden:** observability, load tests, pen-test pass, backups, docs.
10. **Android dev profile** last, as a CPU-only demo.

---

## 8. Honesty clause (baked into product)

This platform can be a genuinely powerful investigative assistant, but Tier-A/B outputs are investigative leads and visual similarity, not courtroom individualization. This disclaimer is baked into the UI and reports so the tool is defensible rather than overstated.

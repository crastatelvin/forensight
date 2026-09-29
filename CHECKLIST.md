# ForenSight — Implementation Checklist

Authoritative progress tracker for `IMPLEMENTATION_PLAN.md`. Every build task, test case, and exit criterion is a checkbox. Do not delete items — only check them. Keep this file in sync with each PR.

**Marker legend:** `[ ]` todo • `[~]` in progress • `[x]` done • `[!]` blocked (add a note)

**Progress:** update the counts when you check items.
- Phases complete: 2 / 11 (P0-P10)
- Build tasks: 14 / 63
- Test cases: 13 / 68

---

## Phase 0 — Foundation & scaffolding
**Build**
- [x] P0.B1 Monorepo layout (`/frontend`, `/backend`, `/agent`, `/workers`, `/infra`, `/docs`, `/tests`)
- [x] P0.B2 `docker-compose.yml` profiles: core, gpu-workers, observability, android-dev (postgres+PostGIS+pgvector, redis, minio, ollama, api, agent, worker)
- [x] P0.B3 FastAPI + Uvicorn/Gunicorn + Pydantic v2 settings (no secrets in code)
- [x] P0.B4 SQLAlchemy 2.0 async engine + Alembic baseline migration
- [x] P0.B5 Tooling: Ruff, mypy, pytest, ESLint/Prettier, pre-commit
- [x] P0.B6 CI: lint -> type-check -> test -> Bandit -> pip-audit -> Semgrep -> Trivy
- [x] P0.B7 Healthchecks `/healthz` (liveness) + `/readyz` (readiness)
**Test cases**
- [x] T0.1 `--profile core up` healthy; `/readyz` 200 only when deps reachable
- [x] T0.2 CI fails on injected Bandit high finding, lint error, failing test (verified indirectly: live CI enforces ruff+bandit+pytest gates and blocked a real starlette HIGH CVE via Trivy before going green)
- [x] T0.3 Alembic upgrade head then downgrade base clean on empty schema
- [x] T0.4 No secret literal (Semgrep/gitleaks); app refuses boot if SECRET_KEY unset (boot-guard verified via test; gitleaks/semgrep run in CI)
- [x] T0.5 android-dev profile boots CPU-only; GPU modules marked unavailable (verified: profile boots api+postgres+redis+minio+ollama healthy on CPU-only runc; /capabilities reports all GPU modules unavailable with reason)
**Exit**
- [x] P0.E clean CI green, all core services healthy, migrations reversible (CI green on main; core + android-dev stacks healthy; migrations reversible verified)

---

## Phase 1 — Auth, RBAC & security baseline
**Build**
- [x] P1.B1 fastapi-users + Argon2id hashing (no plaintext)
- [x] P1.B2 JWT access+refresh; secure/HttpOnly/SameSite cookies; CSRF
- [x] P1.B3 Roles Admin/Officer/Expert/Reviewer + central RBAC dependency
- [x] P1.B4 Redis rate limiting on auth + mutating endpoints
- [x] P1.B5 TLS/HSTS + security headers middleware (CSP etc.)
- [x] P1.B6 Secrets via env/SOPS+age; Vault upgrade path documented
- [x] P1.B7 Seed script for initial admin (no committed DB)
**Test cases**
- [x] T1.1 Argon2id stored; login verifies; wrong password rejected; timing-safe
- [x] T1.2 Expired/invalid/forged JWT rejected; refresh rotation; reused refresh revoked
- [x] T1.3 RBAC matrix parametrized per role per route
- [x] T1.4 Rate limit 429 after threshold; resets after window
- [x] T1.5 CSRF required on cookie mutations; missing/invalid -> 403
- [x] T1.6 Security headers present on all responses
- [x] T1.7 No default/hardcoded creds; seed admin needs env password
**Exit**
- [x] P1.E RBAC enforced+tested; zero plaintext secrets; auth pen-test smoke passes

---

## Phase 2 — Core domain: cases, evidence vault, chain-of-custody, audit
**Build**
- [ ] P2.B1 Schema: users, roles, cases, evidence, evidence_versions, audit_log, findings, entities, timeline_events
- [ ] P2.B2 Evidence ingest: secure_filename + MIME sniff + size limit + ClamAV -> MinIO by SHA-256 in WORM bucket
- [ ] P2.B3 Chain-of-custody records every evidence action
- [ ] P2.B4 Append-only hash-chained audit_log
- [ ] P2.B5 Download-by-object-id with authz (no user paths)
- [ ] P2.B6 Case lifecycle state machine (intake->open->investigating->findings->review->reported->archived)
**Test cases**
- [ ] T2.1 SHA-256 dedupe; hash re-verified on read
- [ ] T2.2 Traversal filename sanitized; download rejects path input, id-only
- [ ] T2.3 Oversized/disallowed MIME rejected
- [ ] T2.4 EICAR rejected by ClamAV; clean passes
- [ ] T2.5 Audit hash chain verifies; tampering detected
- [ ] T2.6 WORM rejects overwrite/delete within retention
- [ ] T2.7 State machine rejects illegal transitions
- [ ] T2.8 Chain-of-custody lists actor/time/action
**Exit**
- [ ] P2.E tamper-evident + reproducible evidence; audit chain verifiable; no path/upload vulns

---

## Phase 3 — Case management API & workflow features
**Build**
- [ ] P3.B1 REST: case CRUD, assignment, status transitions, evidence attach/list, findings CRUD, timeline, entities
- [ ] P3.B2 Full-text search (tsvector) over cases/evidence metadata
- [ ] P3.B3 Notifications (Redis pub/sub) + SLA/status tracking
- [ ] P3.B4 Exports CSV/JSON/ZIP (manifest + metadata, authorized bytes only)
- [ ] P3.B5 OpenAPI published; all inputs Pydantic-validated
**Test cases**
- [ ] T3.1 Case CRUD honors RBAC (officer own / reviewer read-only / admin all)
- [ ] T3.2 Assignment triggers notification; unauthorized assignment blocked
- [ ] T3.3 Full-text search ranked; SQLi neutralized (parametrized)
- [ ] T3.4 CSV/JSON/ZIP match DB; ZIP manifest lists content-hashes
- [ ] T3.5 Illegal status transition -> 409; audit recorded
- [ ] T3.6 OpenAPI contract test: endpoints documented, schemas validated
**Exit**
- [ ] P3.E full case workflow via API with RBAC + audit; exports correct

---

## Phase 4 — Async analysis infrastructure & idempotency
**Build**
- [ ] P4.B1 Celery + Redis broker; worker pool with GPU/CPU capability flags
- [ ] P4.B2 analysis_jobs + analysis_results (queued/running/done/failed)
- [ ] P4.B3 Idempotency key = hash(evidence_hash+module+params+model_version)
- [ ] P4.B4 Module registry plugin interface (name/inputs/params/version/threshold/output/capability)
- [ ] P4.B5 Model registry on MinIO: pinned versions + checksum verify before load
- [ ] P4.B6 Result payload always includes score + threshold + model_version + tier
**Test cases**
- [ ] T4.1 Same input cached; changed version recomputes
- [ ] T4.2 Job lifecycle correct; failed job records error, worker survives
- [ ] T4.3 Checksum mismatch refuses model load
- [ ] T4.4 GPU-only on CPU -> unsupported, not faked
- [ ] T4.5 Result schema carries version+threshold+tier
- [ ] T4.6 N identical jobs collapse to one computation (race)
**Exit**
- [ ] P4.E reproducible, cached, capability-aware pipeline proven with golden inputs

---

## Phase 5 — Agent core (LangGraph) + LLM layer + memory
**Build**
- [ ] P5.B1 LiteLLM adapter: cloud default, Ollama env-switch ready
- [ ] P5.B2 LangGraph state machine (Intake->Planner->ToolExec<=>Memory->Reflector->HITL->Report->Done)
- [ ] P5.B3 Postgres checkpointer (working memory per case/thread)
- [ ] P5.B4 pgvector episodic + semantic memory; embeddings (bge-m3/nomic); scoped case+user+org
- [ ] P5.B5 Structured case-memory tools (findings/entities/timeline = source of truth)
- [ ] P5.B6 Tool registry (typed, Pydantic, audited): run_analysis, query_cases, get_case, update_case_finding, search_evidence, recall_memory, write_memory, generate_report, kb_search, compute_geo, extract_metadata
- [ ] P5.B7 HITL interrupts before any state change
- [ ] P5.B8 Provenance enforcement (cite evidence+analyzer+score+version; block unsourced)
- [ ] P5.B9 Prompt-injection defense (evidence content as untrusted data)
- [ ] P5.B10 Streaming output over WebSocket/SSE
**Test cases**
- [ ] T5.1 Provider switch cloud<->Ollama via env; both complete a run
- [ ] T5.2 Every tool call audited with args + result hash
- [ ] T5.3 HITL pause; approve applies / reject discards; both audited
- [ ] T5.4 Provenance guard rejects/flags unsourced finding
- [ ] T5.5 Injected file instruction does NOT trigger tool calls
- [ ] T5.6 Episodic recall relevant + scope isolated
- [ ] T5.7 Checkpoint resume after kill mid-run
- [ ] T5.8 run_analysis reuses cached result (ties to P4)
- [ ] T5.9 Insufficient-evidence path: agent declines, no fabrication
**Exit**
- [ ] P5.E agent plans/calls tools/cites provenance/respects HITL; memory scoped+resumable; provider-agnostic

---

## Phase 6 — First three real analyzers (proof of "no simulation")
**Build**
- [ ] P6.B1 Face comparison — InsightFace/ArcFace embeddings + cosine + calibrated threshold
- [ ] P6.B2 Object/weapon detection — Ultralytics YOLO pinned weights
- [ ] P6.B3 Image forensics — ELA + EXIF (exiftool) + noise/PRNU
- [ ] P6.B4 Each wired as Celery worker plugin (score+threshold+version+tier)
- [ ] P6.B5 Agent run_analysis invokes all three; results become citable findings
**Test cases**
- [ ] T6.1 Face same-id above / different-id below threshold; deterministic
- [ ] T6.2 No-face image -> explicit "no face detected", not fake score
- [ ] T6.3 YOLO detects expected labels with stable confidences
- [ ] T6.4 Tampered fixture flagged; clean not flagged; EXIF extracted
- [ ] T6.5 Same input twice -> identical result (idempotency hit)
- [ ] T6.6 Determinism guard: no random/np.random in scoring modules
- [ ] T6.7 Threshold + model_version present in every result
**Exit**
- [ ] P6.E three analyzers deterministic golden tests pass; agent uses end-to-end; no-simulation proven

---

## Phase 7 — Frontend (Next.js) + UI/UX
**Build**
- [ ] P7.B1 Next.js App Router + TS + Tailwind + shadcn/ui shared design system
- [ ] P7.B2 Auth flows + role dashboards (Admin/Officer/Expert/Reviewer)
- [ ] P7.B3 Agent console: streaming tokens, tool-call cards, citation chips, HITL buttons
- [ ] P7.B4 Evidence viewer: image/audio/video/text + metadata + side-by-side + ELA overlay
- [ ] P7.B5 Dashboards (Recharts) + MapLibre GL geospatial map
- [ ] P7.B6 Dark/light CSS vars; WCAG 2.1 AA (keyboard/ARIA/focus/contrast)
- [ ] P7.B7 Explainability UX (score meaning, threshold, version, why-popovers)
- [ ] P7.B8 Honesty-clause Tier-A/B disclaimers on results + reports
- [ ] P7.B9 Loading/skeleton/empty/error states; optimistic UI; async progress + notifications
**Test cases**
- [ ] T7.1 E2E: login->create case->upload->run analysis->view cited result->generate report
- [ ] T7.2 Console streams tokens; HITL approve/reject reflects backend
- [ ] T7.3 axe-core AA; keyboard-only flow; contrast checks
- [ ] T7.4 Every Tier-A/B result renders disclaimer+threshold+version
- [ ] T7.5 Long job shows progress + notifies; page responsive
- [ ] T7.6 Dark/light persists; MapLibre real coords; charts match API
- [ ] T7.7 Error/empty states render (no silent failure)
**Exit**
- [ ] P7.E full journey E2E; AA-accessible; disclaimers enforced in UI

---

## Phase 8 — Reports (signed) + provenance appendix
**Build**
- [ ] P8.B1 WeasyPrint/ReportLab PDF; signed PDF (PAdES) + SHA-256 manifest
- [ ] P8.B2 Provenance appendix (evidence id, analyzer, score, threshold, version, timestamp, operator, HITL approvals)
- [ ] P8.B3 Honesty clause + tier disclaimers embedded in report
- [ ] P8.B4 Report export via object-id download (authz) + audit + chain-of-custody
**Test cases**
- [ ] T8.1 PDF signature verifies; alteration breaks signature
- [ ] T8.2 Manifest SHA-256 matches referenced evidence/results
- [ ] T8.3 Provenance appendix full lineage per finding (no unsourced claim)
- [ ] T8.4 Tier disclaimers present; Tier-C only as attached lab results
- [ ] T8.5 Report generation recorded in audit + chain-of-custody
**Exit**
- [ ] P8.E defensible, tamper-evident, fully-sourced reports

---

## Phase 9 — Expand analyzers + Tier-B/Tier-C handling
**Build**
- [ ] P9.B1 Fingerprint — NIST NBIS (MINDTCT+BOZORTH3)
- [ ] P9.B2 Speaker — SpeechBrain ECAPA-TDNN
- [ ] P9.B3 Deepfake — Xception/EfficientNet ONNX
- [ ] P9.B4 Gunshot audio — YAMNet/PANNs
- [ ] P9.B5 Handwriting/document similarity
- [ ] P9.B6 Text authorship/phishing — DistilBERT
- [ ] P9.B7 Geospatial correlation — PostGIS/Shapely
- [ ] P9.B8 Tier-B repositioned as visual-similarity/triage (bloodstain, fiber, glass, paint, soil, hair, iris)
- [ ] P9.B9 Tier-C as lab-attachment workflows only (DNA, tox, pollen, insect, odor, explosive/arson)
**Test cases**
- [ ] T9.1 Each new Tier-A analyzer has deterministic golden tests
- [ ] T9.2 NBIS matching high / non-matching low on fixtures
- [ ] T9.3 Speaker same>threshold / different<threshold
- [ ] T9.4 Tier-B always labeled visual-similarity-not-identity
- [ ] T9.5 Tier-C no AI endpoint; lab-PDF only; AI scoring -> unsupported by design
- [ ] T9.6 Plugin isolation: add module without core change
**Exit**
- [ ] P9.E catalog complete + honestly tiered; every AI module deterministic + sourced

---

## Phase 10 — Hardening, observability, ops, Android dev profile
**Build**
- [ ] P10.B1 OpenTelemetry traces API->agent->worker; Prometheus+Grafana; Loki; Sentry
- [ ] P10.B2 Backups: pg_dump + MinIO replication; documented PITR
- [ ] P10.B3 Graceful shutdown, readiness/liveness, horizontal scaling API+workers
- [ ] P10.B4 Egress control for air-gapped/local-LLM mode
- [ ] P10.B5 Load tests (Locust); pen-test pass; docs (runbooks, threat model, admin guide)
- [ ] P10.B6 Android dev profile: CPU-only ONNX + small Ollama; GPU degraded
**Test cases**
- [ ] T10.1 Trace spans API->agent->worker with correct parent/child
- [ ] T10.2 Prometheus scrapes; Grafana renders; alert fires on synthetic errors
- [ ] T10.3 Backup->restore reproduces DB+evidence; hashes verify
- [ ] T10.4 Locust: queue sustains throughput; no job loss; latency SLO
- [ ] T10.5 Air-gapped: outbound blocked; local Ollama serves agent
- [ ] T10.6 Android profile runs CPU analyzer + agent turn; GPU reports unavailable
- [ ] T10.7 Graceful shutdown drains jobs; readiness flips before termination
**Exit**
- [ ] P10.E observable, recoverable, load-tested, air-gap-capable; Android demo runs; docs complete

---

## Cross-cutting quality gates (enforced every phase)
- [ ] X1 pytest unit+integration on testcontainers Postgres
- [ ] X2 Playwright E2E
- [ ] X3 Contract tests for agent tools
- [ ] X4 Agent eval harness (cites sources, respects HITL)
- [ ] X5 Golden tests for every analyzer (no simulation)
- [ ] X6 CI security: Bandit, Semgrep, pip-audit, Trivy, gitleaks (fail on high)
- [ ] X7 Coverage threshold gate; mypy strict; Ruff/ESLint clean

---

## Future improvements (see FUTURE_IMPROVEMENTS.md)
- [ ] FI-1 Case timeline auto-assembly
- [ ] FI-2 Duplicate / near-duplicate evidence detection
- [ ] FI-3 Redaction tool for exports
- [ ] FI-4 Multi-case entity linking
- [ ] FI-5 Agent second-opinion reviewer mode

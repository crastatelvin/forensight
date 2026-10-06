# ForenSight — Implementation Checklist

Authoritative progress tracker for `IMPLEMENTATION_PLAN.md`. Every build task, test case, and exit criterion is a checkbox. Do not delete items — only check them. Keep this file in sync with each PR.

**Marker legend:** `[ ]` todo • `[~]` in progress • `[x]` done • `[!]` blocked (add a note)

**Progress:** update the counts when you check items.
- Phases complete: 6 / 11 (P0-P10)
- Build tasks: 49 / 65
- Test cases: 51 / 70

---

## Phase 0 — Foundation & scaffolding
**Build**
- [x] P0.B1 Monorepo layout (`/frontend`, `/backend`, `/agent`, `/workers`, `/infra`, `/docs`, `/tests`)
- [x] P0.B2 `docker-compose.yml` profiles: core, gpu-workers, observability, android-dev (postgres+PostGIS+pgvector, redis, minio, ollama, api, agent, worker)
- [x] P0.B3 FastAPI + Uvicorn/Gunicorn + Pydantic v2 settings (no secrets in code)
- [x] P0.B4 SQLAlchemy 2.0 async engine + Alembic baseline migration
- [x] P0.B5 Tooling: Ruff, mypy, pytest, pre-commit
- [!] P0.B5a ESLint/Prettier — blocked on Phase 7 (no frontend yet)
- [x] P0.B6 CI: lint -> type-check -> test -> Bandit -> pip-audit -> Semgrep -> Trivy
- [x] P0.B7 Healthchecks `/healthz` (liveness) + `/readyz` (readiness)
- [x] P0.B8 `/metrics` Prometheus endpoint (the scrape config already pointed here; it 404'd)
**Test cases**
- [x] T0.1 `--profile core up` healthy; `/readyz` 200 only when deps reachable (CI job `compose-smoke`)
- [x] T0.2 CI fails on injected Bandit high finding, lint error, failing test
- [x] T0.3 Alembic upgrade head then downgrade base clean on empty schema (CI job `integration`)
- [x] T0.4 No secret literal (Semgrep/gitleaks); app refuses boot if SECRET_KEY unset
- [x] T0.5 android-dev profile boots CPU-only; GPU modules marked unavailable
**Exit**
- [x] P0.E clean CI green, all core services healthy, migrations reversible

---

## Phase 1 — Auth, RBAC & security baseline
**Build**
- [x] P1.B1 fastapi-users + Argon2id hashing (no plaintext)
- [x] P1.B2 JWT access+refresh; secure/HttpOnly/SameSite cookies; CSRF
- [x] P1.B3 Roles Admin/Officer/Expert/Reviewer + central RBAC dependency
- [x] P1.B4 Redis rate limiting on auth + API routes
- [x] P1.B5 TLS/HSTS + security headers middleware (CSP etc.)
- [x] P1.B6 Secrets via env/SOPS+age; Vault upgrade path documented
- [x] P1.B7 Seed script for initial admin (no committed DB)
- [x] P1.B8 CORS for the Next.js origin (preflight was 405 with no ACAO header)
- [x] P1.B9 Session revalidation — disabled/deleted/role-changed accounts lose access immediately
**Test cases**
- [x] T1.1 Argon2id stored; login verifies; wrong password rejected; timing-safe
- [x] T1.2 Expired/invalid/forged JWT rejected; refresh rotation; reused refresh revoked
- [x] T1.3 RBAC matrix parametrized per role per route
- [x] T1.4 Rate limit 429 after threshold; resets after window
- [x] T1.5 CSRF required on cookie mutations; missing/invalid -> 403
- [x] T1.6 Security headers present on all responses
- [x] T1.7 No default/hardcoded creds; seed admin needs env password
- [x] T1.8 Cookie auth round-trips end to end (login sets cookies; CSRF echo succeeds)
- [x] T1.9 Deactivated user / changed role invalidates an existing token
**Exit**
- [x] P1.E RBAC enforced+tested; zero plaintext secrets; auth pen-test smoke passes

---

## Phase 2 — Core domain: cases, evidence vault, chain-of-custody, audit
**Build**
- [x] P2.B1 Schema: users, roles, cases, evidence, evidence_versions, audit_log, findings, entities, timeline_events
- [x] P2.B2 Evidence ingest: secure_filename + MIME sniff + size limit + ClamAV -> MinIO by SHA-256 in WORM bucket
- [x] P2.B3 Chain-of-custody records every evidence action
- [x] P2.B4 Append-only hash-chained audit_log
- [x] P2.B5 Download-by-object-id **with case-scope authz** (object-level, not just role-level)
- [x] P2.B6 Case lifecycle state machine (intake->open->investigating->findings->review->reported->archived)
- [x] P2.B7 Audit chain serialized against concurrent forking (advisory lock)
- [x] P2.B8 Integrity-failure audit is committed, not rolled back with the 409
**Test cases**
- [x] T2.1 SHA-256 dedupe; hash re-verified on read
- [x] T2.2 Traversal filename sanitized; download rejects path input, id-only
- [x] T2.3 Oversized/disallowed MIME rejected (streamed cap, enforced before buffering)
- [x] T2.4 EICAR rejected by AV; clean file passes (NoopScanner + ClamAV client)
- [x] T2.5 Audit hash chain verifies; tampering detected
- [x] T2.6 WORM rejects overwrite/delete within retention (live MinIO, runs in CI)
- [x] T2.7 State machine rejects illegal transitions
- [x] T2.8 Chain-of-custody lists actor/time/action
- [x] T2.9 Cross-case access denied: officer/expert cannot read another user's case or evidence bytes
- [x] T2.10 Tampered evidence produces a durable audit record
**Exit**
- [x] P2.E tamper-evident + reproducible evidence; audit chain verifiable; no path/upload vulns

---

## Phase 3 — Case management API & workflow features
**Build**
- [x] P3.B1 REST: case CRUD + patch, assignment, status transitions, evidence attach/list, findings CRUD, timeline write+read, entities CRUD
- [x] P3.B2 Full-text search (tsvector) over cases, scope-filtered
- [x] P3.B3 Notifications (Redis pub/sub) + case metadata (priority/due_at) settable and readable
- [x] P3.B4 Exports CSV/JSON/ZIP (manifest + metadata, authorized bytes only)
- [x] P3.B5 OpenAPI published; all inputs Pydantic-validated
- [x] P3.B6 Pagination + bounds on every list endpoint
- [x] P3.B7 Audit read + chain-verification endpoints (admin-only)
- [x] P3.B8 Content-Disposition injection closed (case number validated on write)
- [x] P3.B9 Per-case access control (owner/assignee scoping) in `app/domain/access.py`
- [x] P3.B10 Assignment validates the assignee exists; entity creation audited
**Test cases**
- [x] T3.1 Case CRUD honors RBAC *and* per-case scope (officer/expert own-or-assigned, reviewer read-only, admin all)
- [x] T3.2 Assignment triggers notification; unauthorized assignment blocked; assignment visible in response
- [x] T3.3 Full-text search ranked; SQLi neutralized (parametrized); scoped to visible cases
- [x] T3.4 CSV/JSON/ZIP match DB; ZIP manifest lists content-hashes
- [x] T3.5 Illegal status transition -> 409; audit recorded
- [x] T3.6 OpenAPI contract test: endpoints documented, schemas validated
- [x] T3.7 Pagination bounds enforced; pages disjoint
- [x] T3.8 Audit endpoints admin-only; chain verifies over many appends
- [x] T3.9 Notification failure (Redis down) does not fail a committed request
- [x] T3.10 Cross-case access denied (404) for read, write, download, custody, export
- [x] T3.11 Search and case listing scoped to visible cases
- [x] T3.12 Assignment to a non-existent user -> 422
**Exit**
- [x] P3.E full case workflow via API with RBAC + per-case access control + audit; exports correct

---

## Phase 4 — Async analysis infrastructure & idempotency
**Build**
- [x] P4.B1 Dramatiq + Redis broker; CPU worker (`worker`) and GPU worker (`worker-gpu`)
- [x] P4.B2 analysis_jobs + analysis_results (queued/running/done/failed/unsupported)
- [x] P4.B3 Idempotency key = hash(evidence_hash+evidence_id+module+params+model_version), UNIQUE in the DB
- [x] P4.B4 Module registry plugin interface (name/inputs/params/version/threshold/output/capability)
- [x] P4.B5 Model registry on MinIO: pinned versions + checksum verify before load
- [x] P4.B6 Result payload always includes score + threshold + model_version + tier + disclaimer
**Test cases**
- [x] T4.1 Same input cached; changed version recomputes
- [x] T4.2 Job lifecycle correct; failed job records error, worker survives
- [x] T4.3 Checksum mismatch refuses model load
- [x] T4.4 GPU-only on CPU -> unsupported, not faked
- [x] T4.5 Result schema carries version+threshold+tier
- [x] T4.6 N identical jobs collapse to one computation (race)
**Exit**
- [x] P4.E reproducible, cached, capability-aware pipeline proven with golden inputs
      (verified against live Postgres/MinIO/Redis/worker; `byte-entropy` golden values 0.0 and 8.0)

---

## Phase 5 — Agent core + LLM layer + memory
**Build**
- [x] P5.B1 ChatModel protocol + OpenAI-compatible adapter; cloud/Ollama chosen by env alone
      (deviation: not LiteLLM — every target provider speaks `/v1`, httpx already ships)
- [x] P5.B2 State machine Intake->Planner->ToolExec<=>Memory->Reflector->HITL->Report->Done
      (deviation: not LangGraph — interrupts must be audited, and the checkpointer is ours either way)
- [x] P5.B3 Postgres checkpointer: append-only `agent_checkpoints`, resumable from last step
- [x] P5.B4 pgvector memory (vector(1024) + HNSW), episodic/semantic, scoped by case+actor
- [x] P5.B5 Structured case-memory tools (findings/entities/timeline remain the source of truth)
- [x] P5.B6 Tool registry: typed Pydantic args, audited with arg+result hashes
      (`generate_report`/`kb_search`/`compute_geo` deliberately absent — Phase 8/9, not stubbed)
- [x] P5.B7 HITL gate on every state-changing tool; approve/reject both audited
- [x] P5.B8 Provenance enforcement: citations resolved against stored results; unsourced blocked
- [x] P5.B9 Prompt-injection defense: sanitise + untrusted-data wrapping + SQL-enforced scope
- [x] P5.B10 SSE progress stream
**Test cases**
- [x] T5.1 Provider selected by env alone; unconfigured fails loudly rather than defaulting
- [x] T5.2 Every tool call audited with arg hash + result hash, failures included
- [x] T5.3 HITL pause; approve applies / reject discards; both audited; approval not replayable
- [x] T5.4 Provenance guard rejects unsourced, hallucinated, cross-case and paraphrased claims
- [x] T5.5 Injected evidence instruction does NOT trigger tool calls
- [x] T5.6 Episodic recall ranked by similarity; case+actor scope isolated in the SQL predicate
- [x] T5.7 Checkpoint resume after a simulated process kill
- [x] T5.8 run_analysis reuses cached result (ties to P4)
- [x] T5.9 Insufficient-evidence path: agent declines, no fabrication
- [x] T5.10 Live run against a real provider
      `tests/test_agent_live.py`, skipped unless `FORENSIGHT_LLM_LIVE=1`. Every other
      agent test drives `ScriptedModel`, which proves the graph *handles* tool calls;
      this proves a model *makes* them. Covers both bugs above: the wire format, and
      the tool result actually being legible to the model.
**Exit**
- [x] P5.E agent plans, cites provenance, respects HITL; memory scoped and resumable;
      provider-agnostic. Verified end-to-end against a live model: the graph ran to a recorded
      refusal rather than an unsourced report.
      [x] Tool-loop gap CLOSED. It was never a model-size problem — `qwen2.5:3b`
      drives the loop fine. Two real bugs, neither of which raised:
      1. **Tools were sent in the wrong wire format.** `tool_catalog()` emits flat
         entries; the OpenAI `/chat/completions` API needs
         `{"type":"function","function":{...}}`. Flat entries are accepted without
         error and *silently ignored* — the model is never told the tools exist, so
         it answers in prose. Indistinguishable from "the 3B can't use tools".
         Confirmed live: flat → `finish=stop, tool_calls=0`; wrapped →
         `finish=tool_calls`. Fixed by `to_model_tools()` plus normalisation in
         `OpenAICompatModel.complete` so no caller can reintroduce it.
      2. **Tool results never reached the model.** Results were appended as a user
         message with the payload in a `result` key that the message builder never
         read, so the model received the literal string
         `"Tool search_evidence returned:"` and nothing else. It had no way to learn
         anything and repeated the same call until `MAX_TOOL_CALLS` stopped it — 20
         identical calls observed live. The audit rows were correct throughout, so
         every scripted test passed. Fixed to emit the assistant `tool_calls` turn
         plus a `role: "tool"` turn with `tool_call_id`, matching the API.
      Live proof: `tests/test_agent_live.py` (`FORENSIGHT_LLM_LIVE=1`) runs the
      graph against a real provider and asserts the model itself made the call and
      produced a *sourced* report — citation score 0.420, threshold 0.010, tier A,
      evidence and result ids intact. Result: `completed`, 1 tool call.
      [!] The 3B model's prose is still weak — it expanded ELA as "Evasion
      Learning Algorithm" (it is Error Level Analysis). The citation and score are
      correct because they are rebuilt from the stored row, not from model text,
      which is the point of the provenance guard. A >=7B model is still worth using
      for report readability, but tool use is no longer the blocker.

---

## Phase 6 — First three real analyzers (proof of "no simulation")
**Build**
- [ ] P6.B1 Face comparison — InsightFace/ArcFace embeddings + cosine + calibrated threshold
- [~] P6.B2 Object detection — YOLOv4-tiny, pinned ONNX weights. **No weapon
      detection, and this is a real limitation rather than a tuning gap.**
      COCO has no firearm/rifle/pistol class at all, so this model cannot detect
      the objects this platform most often needs to find. The default watchlist is
      therefore `knife` + `scissors`, the only edged objects COCO knows, and both
      the disclaimer and `known_limitations` state the gap. Weapon detection needs
      a model trained on weapons.
      - **Deviation: YOLOv4-tiny from the ONNX model zoo, not Ultralytics YOLO.**
        Runs on `onnxruntime` (no torch, no ultralytics dependency) and pins the
        official export by digest. The checklist named Ultralytics; the ONNX export
        of the same architecture is defensible and installs in seconds rather than
        pulling torch.
      - Weights are 257 MB, so `backend/scripts/fetch_models.sh` fetches and
        **digest-verifies** them. CI runs it; a local checkout without them skips
        the detection tests rather than failing.
      - The decode is written out in the module because the output is *already*
        sigmoid-activated. A second sigmoid is the easy mistake: it still returns
        boxes in plausible places and just flattens every confidence toward 0.5.
        Golden confidences are pinned, plus a regression test that feeds 0..255
        input and asserts the decoder produces near-zero-area boxes — that mistake
        does not raise, it returns ~160 confident detections with no geometry.
      - `intra_op_num_threads=1` is a determinism requirement, not a performance
        choice: multi-threaded reduction order otherwise moves boxes between runs.
      - `0.0` from this module **is** a real measurement (the model looked and found
      nothing), which is the opposite of ELA-on-a-PNG. `is_zero_meaningful` in the
      payload says so, and points at the COCO gap.
      - **Honest test limitation:** object detection cannot be verified against a
        generated fixture. `make_photo_of_objects` draws a rectangle with two discs
        and the detector correctly reports nothing, because a drawing is not a bus.
        Tuning a threshold until a synthetic shape registered would be tuning the
        test to pass. So one 137 KB photograph is committed with a pinned SHA-256
        (`tests/assets/bus.jpg`, the canonical YOLO test image) and the test asserts
        against what it actually produces: `person` 0.987, `person` 0.975,
        `bus` 0.965. The negative control (a flat grey frame yields zero
        detections) is still generated.
- [~] P6.B3 Image forensics — ELA + EXIF done; **noise/PRNU not built**
      Two modules rather than one, because the module contract requires a single
      scalar score and ELA (compression anomaly) and EXIF (metadata indicators) do
      not share a scale. PRNU is a third technique with its own requirements and
      is genuinely absent, not stubbed.
      - `image-ela` — Tier A. Anomaly detection uses median + MAD, not mean + std:
        the regions ELA hunts for are exactly the ones that would inflate the mean.
        `model_version` embeds `libjpeg-turbo`, because a codec upgrade silently
        changes every score and would otherwise serve stale results under an
        unchanged label. Pins to numpy + Pillow, no GPU, no weights download.
      - `image-exif` — Tier B. Score is the fraction of *scored* indicators, and
        `score_is` says so in the payload; it is not a probability. GPS and
        `Software` are reported in full but deliberately **not scored** — an
        untouched camera photo carries both (own firmware string, location data),
        so scoring them would flag clean evidence. `Software` would also mean
        guessing which applications are editors.
- [x] P6.B4 Each wired as a worker plugin (score+threshold+version+tier)
      Registered in `register_all()`, which the Dramatiq worker calls at import.
      NOTE: the checklist said Celery; the project uses Dramatiq (see Phase 4).
**Test cases**
- [ ] T6.1 Face same-id above / different-id below threshold; deterministic
- [~] T6.2 No-face image -> explicit "no face detected", not fake score
      The *mechanism* is built and proven for the image analyzers: new
      `AnalysisNotApplicable` exception + `not_applicable` terminal job status,
      which writes **no result row**, so "cannot measure" can never render as
      "measured zero". Exercised end-to-end (ELA on PNG) and on live Postgres.
      The face-specific case remains unbuilt pending P6.B1.
- [x] T6.3 Detects expected labels with stable confidences
      `person` 0.987 / `person` 0.975 / `bus` 0.965 on a digest-pinned photo,
      asserted as golden values. Flat-grey negative control yields zero detections.
      Regression test pins the decoder's behaviour on wrongly-scaled input.
- [x] T6.4 Tampered fixture flagged; clean not flagged; EXIF extracted
      Clean 0.0; tampered 0.0505 against a 0.01 threshold; stripped metadata 1.0.
      Golden scores pinned. Two candidate EXIF indicators were written and
      removed for firing on ordinary photos — a threshold that flags clean
      evidence is worse than no threshold.
- [x] T6.4b Verified model bytes reach the module
      Found while writing P6.B2: the executor called `load_verified(...)` and
      **threw the bytes away**, then invoked `module(data, params)`. A module
      needing weights would have had to fetch its own by another route, so the
      checksum check would attest to an artifact nothing used. Fixed by passing
      the verified bytes as a keyword, with two tests: one asserts the module
      receives exactly the bytes whose digest was pinned, one asserts a
      pure-algorithm module still runs with `model_bytes=None`.
- [x] T6.5 Same input twice -> identical result (idempotency hit)
      Same submission returns the same job with `attempts == 0`, asserted through
      the HTTP API, plus byte-identical fixture generation.
- [x] T6.6 Determinism guard: no random/np.random in scoring modules
      Rewritten: it previously banned `numpy` outright, which real image work
      needs. numpy arithmetic is deterministic; `np.random` is not. Now parses the
      AST for random/time/uuid/secrets/os entry points. Two self-tests assert the
      scanner both catches known-bad source and allows legitimate numpy — a guard
      that cannot fail is not a guard.
- [x] T6.7 Threshold + model_version present in every result
      `build_result` injects these, so no module can emit a result without them.
**Exit**
- [ ] P6.E three analyzers deterministic golden tests pass; agent uses end-to-end; no-simulation proven
      Two of three analyzers built (image forensics, object detection). Face
      comparison remains. The agent side is now met: a live 3B model drives the tool
      loop and produces a sourced report (T5.10). Still not earned.

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
- [ ] P10.B1 OpenTelemetry traces API->agent->worker; Prometheus+Grafana; Loki; Sentry (Prometheus + Loki config done)
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
- [x] X1 pytest unit+integration on live Postgres + MinIO (CI `integration` job)
- [ ] X2 Playwright E2E
- [ ] X3 Contract tests for agent tools
- [ ] X4 Agent eval harness (cites sources, respects HITL)
- [ ] X5 Golden tests for every analyzer (no simulation)
- [ ] X6 CI security: Bandit, Semgrep, pip-audit, Trivy, gitleaks (pip-audit still advisory)
- [x] X7 Ruff lint + format clean; mypy strict on `app`
- [x] X8 Compose core profile boots, migrates, and serves in CI

---

## Future improvements (see FUTURE_IMPROVEMENTS.md)
- [ ] FI-1 Case timeline auto-assembly
- [ ] FI-2 Duplicate / near-duplicate evidence detection
- [ ] FI-3 Redaction tool for exports
- [ ] FI-4 Multi-case entity linking
- [ ] FI-5 Agent second-opinion reviewer mode
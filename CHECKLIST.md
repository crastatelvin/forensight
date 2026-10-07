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
- [x] P0.B5a ESLint/Prettier — both wired and enforced in CI
      ESLint 9 flat config over `eslint-config-next` 16 (flat-native, so imported
      directly rather than through `FlatCompat`, which crashes on it), plus
      Prettier. A `frontend` CI job runs `typecheck`, `lint`, `format:check` and
      `build`. It found real dead code on first run — four unused bindings and two
      `<a>` elements where Next wants `<Link>` — and two instances of the
      `set-state-in-effect` anti-pattern, both replaced with the idiomatic fix
      rather than suppressed.
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
- [x] P6.B1 Face comparison — **verification (1:1), not identification**
      Two pinned Apache-2.0 models: YuNet detects, SFace embeds, cosine compared
      against SFace's published 0.593 operating point. Tier B.
      - **Deviation: SFace from the OpenCV model zoo, not InsightFace/ArcFace.**
        InsightFace's pretrained weights are labelled non-commercial research, so
        that was the initial reason. The owner has since ruled that this project is
        open source and that reason does not hold — the substitution stands on SFace
        being the smaller, Apache-2.0 dependency that installs without torch, not on
        a licensing judgement. If ArcFace accuracy matters later, switching is a
        digest change in this module. SFace is the weaker model and that is a real
        cost.
      - Two models, so `ModuleSpec.aux_models` was added: additional pinned
        artifacts, each verified against its own digest, fetched as
        `<model_key>#<name>`. Bundling into one archive would make the pinned
        digest cover a container format too.
      - A comparison needs two images, so `params["reference_evidence_id"]` names
        the second one. Resolved **case-scoped** — without the `case_id` predicate
        it would be a cross-case read of image bytes. Both images are
        hash-verified on the same rule as the primary evidence.
      - **The threshold is the vendor's, not ours, and the payload says so.**
        Calibrating an operating point needs thousands of labelled pairs from the
        deployment's own population; four same-identity pairs catch a broken
        decoder and nothing more. `calibrated_on_this_deployment: false` ships in
        every result.
      - Verification only, and that is also the legal boundary: one-to-many search
        against a face database is restricted or prohibited in several
        jurisdictions, so this module deliberately cannot do it.
- [!] P6.B2 Object detection — YOLOv4-tiny, pinned ONNX weights. Built and
      tested; **no weapon detection, and that is a real capability gap rather than
      a tuning problem.**
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
- [x] P6.B3 Image forensics — ELA + EXIF + PRNU, three modules
      Three rather than one, because the module contract requires a single scalar
      score and these three do not share a scale: ELA scores a compression anomaly,
      EXIF a metadata-integrity ratio, PRNU a fingerprint correlation.
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
      - `image-prnu` — Tier B. Sensor-fingerprint correlation (Photo Response
        Non-Uniformity): does this frame share a camera body with the reference?
        **It says nothing about the people in it.** Two people shot on one camera
        correlate as a match; one person shot on two cameras does not.
        Haar residual, mean-centred; `THRESHOLD = 0.20` measured on this repo's
        fixtures and explicitly *not* calibrated — correlation thresholds are
        specific to a camera population and imaging pipeline. Measured on
        fixtures: same sensor 0.276, three different sensors 0.011 / 0.011 /
        0.009.
        PRNU is the third image module, so P6.B3 is now complete: ELA, EXIF and
      PRNU — each its own module, because they do not share a score scale.
      Two fixture bugs found and fixed while building it, both of which made the
        technique look broken rather than mis-specified:
        - A *smooth* trigonometric "fingerprint" scored 0.008, because the
          estimator reads the finest wavelet band and a smooth pattern is not in
          it. Real PRNU is per-pixel; the fixture now is too.
        - A checkerboard texture term at amplitude ~22 sat in the same band as
          the signal and swamped it. Scene content must be smooth for the
          fingerprint to be visible, which is also why real PRNU needs many frames
          and why real thresholds are dataset-specific.
- [x] P6.B4 Each wired as a worker plugin (score+threshold+version+tier)
      Registered in `register_all()`, which the Dramatiq worker calls at import.
      NOTE: the checklist said Celery; the project uses Dramatiq (see Phase 4).
**Test cases**
- [x] T6.1 Face same-id above / different-id below threshold; deterministic
      Turing 1936 vs 1951 = **0.7377**, Obama portraits = **0.8128**, three
      different-identity pairs = **-0.093, -0.046, -0.025**. A separate test
      asserts the whole same-id population outranks the whole different-id
      population — if they ever overlap, no threshold exists and the test says so.
      Fixtures are real public-domain photographs (two per subject, so the
      same-identity case is two *different* photographs, not one file against
      itself), digest-pinned in `tests/assets/`. Synthetic faces are useless here:
      a model scores a drawing as confidently as a photograph.
      **Bug this caught:** the YuNet export returns `[box, kps(10), score]` — score
      *last*. OpenCV's docs say score at index 4. Trusting the docs made
      `alignCrop` warp from a score value, producing blank white crops (std=0.0)
      and embeddings with no same/different structure at all — every pair landed
      near 0.78–0.99. `row_layout_is_landmarks_then_score` now asserts the
      anatomy (landmarks inside the box, eyes level, nose and mouth below) plus the
      score's 0..1 range, and there is a test that the *documented* layout is
      rejected.
- [x] T6.2 No-face image -> explicit "no face detected", not fake score
      Mechanism built and proven for the image analyzers: new
      `AnalysisNotApplicable` exception + `not_applicable` terminal job status,
      which writes **no result row**, so "cannot measure" can never render as
      "measured zero". Exercised end-to-end (ELA on PNG), on live Postgres, and for
      faces (featureless image, and a missing reference image).
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
- [x] P6.E three analyzers deterministic golden tests pass; agent uses end-to-end; no-simulation proven
      Six analyzers, real pinned weights, deterministic golden tests, refusals where a
      technique does not apply — and a live `qwen2.5:3b` that calls `get_case`, calls
      `run_analysis`, gets a real score back, and proceeds to a report. Verified:
      `tools called: ['get_case', 'run_analysis']`, then the HITL gate parks
      `update_case_finding` for a human. No step of that is simulated.

      The remaining gap was **three bugs of the same shape**, all invisible to green
      tests because `ScriptedModel` replays responses regardless of what it is sent:
      1. `run_analysis` returned `"analysis is asynchronous; re-read results before
         citing"` — an instruction the loop could not carry out. The only tool that
         shows results is `search_evidence`, which the model must think to call and
         which reads whatever exists *at the time it runs*, so a result produced
         afterwards was invisible for the rest of the run. The agent therefore always
         declined for lack of results. Now: with no broker it runs the job inline
         via the same `execute_job` the worker calls and returns a citable payload;
         with a broker it says plainly that no result is available yet. Only the
         transport differs — verification and refusal statuses are identical.
      2. `approve()` parked the approved action's outcome under the same unread
         `"result"` key as the original bug, so after a human approved something the
         model still could not see that it had happened. Fixed to the assistant
         `tool_calls` + `role: "tool"` turn shape.
      3. A cache hit returned a *different shape* from a cold run, so `cached` had to
         be inspected to know whether a result existed — inviting a reader to treat a
         cache hit as "no result". Both shapes are now identical.

      Because the pattern recurred three times, the last assertion in
      `tests/test_agent_tool_roundtrip.py` is structural: **no message may park a
      tool outcome anywhere but `content`**. That guard would have caught all three.

      [!] One honest limitation remains, and it is a model limit rather than a system
      one: a 3B model invents `evidence_id="attached_image"` when asked to analyse
      "the attached image". Pointed at an explicit id in the objective and told to
      call `get_case` first, it copies the UUID correctly. A production path should
      still take an evidence *index* rather than an id, so the model is never asked
      to transcribe one.

**Bugs found in this phase, all by running the stack**
Ten of these were invisible to the test suite and none of them raised. The last
five were found by running the deployed stack — compose, real Postgres, real
MinIO, a real worker — not by any test:
- `_ensure_tools()` registered the *agent* tool registry but not the *analysis*
  registry, so `run_analysis` raised `KeyError: unknown analysis module` for every
  module. The agent was handed a tool it could never use, and every failure looked
  like the model choosing badly.
- `run_analysis` told the model to re-read results when no tool existed to do that.
- `approve()` sent the approved action's outcome into a key `_reflector` never read,
  so the model could not learn that an approved write had happened.
- Tools were sent to the provider as a flat catalog rather than the OpenAI
  `{"type": "function", "function": {...}}` shape, which every OpenAI-compatible
  endpoint accepts and silently ignores. The model was never told the tools existed.
- Tool results were parked under a `"result"` key on a user message, so the model
  received the literal string "Tool search_evidence returned:" and nothing else. It
  repeated the same call 20 times against a live provider.
- A cache hit returned a different response shape from a cold run.
- **The worker reused the module-level engine pool.** A pooled asyncpg connection
  is bound to the event loop that first used it, and Dramatiq's per-job
  `asyncio.run` makes a new loop each time, so every job after the first died with
  `got Future ... attached to a different loop`. The first job succeeded, which is
  exactly what made it look intermittent.
- **No execution path passed a `ModelStore`.** `object-detect` and `face-compare`
  worked in tests and were unusable in the deployed worker *and* in the inline run
  endpoint — both failed with "needs a verified model but no store was provided".
  The `models` bucket also had to be provisioned by hand; there is now
  `scripts/provision_models.py`, idempotent, and a missing bucket is reported as an
  operator-facing refusal instead of a raw S3 stack.
- **A duplicate case number returned 500, not 409**, blaming the server for the
  caller's duplicate and leaking a stack trace through the body. The suite generates
  a fresh case number per test, so nothing ever collided. Now handled app-wide, and
  the message names the violated constraint where the driver exposes one.
- **`/capabilities` had drifted from the code**: it advertised three GPU analyzers
  that do not exist (ArcFace, deepfake, speaker embeddings) and omitted the five
  that do — including reporting CPU-runnable `face-compare` as
  `face-recognition-gpu: unavailable`. The two tests that "covered" it asserted the
  stale list, so they passed while the endpoint lied. Now built from the analysis
  registry, with versions.
- **Security**: Pillow 11.1.0 carried **17 known CVEs** on the image-decoding path
  every untrusted upload takes; pytest 8.3.4 had one more. Both upgraded. The
  pip-audit gate was `|| true` with a comment about an uncleared CVE backlog — that
  backlog is clear, so `|| true` is gone. It is `scripts/audit_deps.py`, which
  extracts direct requirements (pip-audit cannot resolve this project, installed
  editable and unpublished) and was verified to fail on a deliberately downgraded
  Pillow, so the gate has teeth. Bandit also only scanned `app`, never `workers` —
  which is why the `assert`-stripped-under-`-O` checks in the audit writer went
  unflagged; those are now real `TypeError`s.
- YuNet's row layout is `[box, kps(10), score]` — score **last**. OpenCV's docs
  say index 4. Trusting the docs produced blank white crops (std=0.0) and
  embeddings with no same/different structure at all.
- The determinism guard banned the whole `os` module, so a module could not delete
  a temp file. `os.urandom` is still banned; the module is not.

---

## Phase 7 — Frontend (Next.js) + UI/UX
**Build**
- [x] P7.B1 Next.js App Router + TS + Tailwind + shared design system
      **shadcn/ui is not used.** The plan names it, and this is a recorded
      deviation rather than an oversight: the app needs a button, card, badge,
      notice, empty state, tab strip, dropdown and a `<dl>`. shadcn would add a
      CLI, a `components.json`, Radix for four of those (a tab strip and a
      dropdown are ~60 lines of ARIA, and the rest have no behaviour to share), and
      a `cn()` helper — for a set of primitives that would all fit in one file of
      the same code. `src/components/ui.tsx` is that file. Every primitive carries
      its own semantics (`role`, `aria-*`, `<dl>`/`<th scope>`), which is where the
      accessibility work lives anyway; a Radix primitive would supply the same
      semantics with more indirection.
      **Revisit if a component with real behaviour is needed** — a combobox, a
      modal, a popover with focus trapping. At that point shadcn's Radix primitive
      is cheaper than hand-rolling focus management, and this decision should flip.
- [x] P7.B2 Auth flows + role dashboards (Admin/Officer/Expert/Reviewer)
- [x] P7.B3 Agent console: streaming tokens, tool-call cards, citation chips, HITL buttons
      Verified live end to end against `qwen2.5:3b`. Observed run: the agent called
      `get_case`, then `run_analysis`, then **declined** — "no analyzer results
      exist for this case yet" — because the queued analysis had not landed when it
      looked. That refusal is the honesty contract working: it declined rather than
      asserting something it could not cite, and it is what this tab renders most
      prominently.

      SSE is proxied through a route handler, because the browser cannot add an
      `Authorization` header to an `EventSource` and the access cookie is
      HttpOnly. Tool calls render with their `args_hash` and `result_hash`; claims
      render with citation chips carrying the analyzer, the score against its own
      threshold, and the evidence it came from. **The HITL approve/reject round trip
      has now been driven from the browser, after a reload: the gate reappeared on
      the parked run, showed the exact arguments, and approving returned in 37ms
      with the finding on the case (`findings 0 -> 1`).
- [x] P7.B4 Evidence viewer: image/audio/video/text + metadata + side-by-side + ELA overlay
- [x] P7.B5 Result overview + geospatial view. **Neither Recharts nor MapLibre GL,
      and both deviations are deliberate.**
      - **No MapLibre, and no basemap.** The obvious implementation is MapLibre with
        raster tiles, and it is wrong for this system: a tile request carries the
        viewport, and the viewport here is derived from the evidence. Panning far
        enough would tell a tile CDN where the photographs were taken, before anyone
        has decided that is acceptable to disclose — and in a restricted deployment it
        renders a blank canvas. So the coordinates are plotted on an equirectangular
        graticule with no external service contacted, and the panel says so.
        *Flip to MapLibre* when a deployment supplies its own tile source (internal
        server, licensed offline basemap, or explicit opt-in). That is a rendering
        change only — the coordinates are already in decimal degrees.
      - **No Recharts.** Each analyzer has its own scale — entropy in bits per byte,
        face similarity a cosine — so a shared axis would be the single most
        misleading thing this screen could do, and the rows are therefore bars
        scaled per-analyzer with the threshold ticked on the same axis. More
        decisively: the reader is deciding whether something needs investigating, so
        score and threshold must be readable as *text* by a screen reader and
        copyable into a report. Recharts renders SVG a screen reader reads as an
        unlabelled graphic, so an accessible table would be needed alongside it
        anyway — the same information twice, the second copy second-hand. Each row is
        a real DOM element whose fill is a `div`.
        *Flip to a charting library* for a genuine time series — score against
        analysis time across a case, dozens of points, a readable x-axis. That is a
        different chart and not this one.
      - **GPS is now extracted to decimal degrees** by `image-exif`, with the raw
        DMS triples carried alongside so the decimal figure can be re-derived by
        hand. Hemisphere refs are honoured (an implementation that ignores them puts
        London in the southern eastern hemisphere, which is a real place in the wrong
        hemisphere). Malformed components yield no coordinate rather than a guess at
        (0, 0) — that is a point in the Gulf of Guinea, which would look like data.
        Still never scored: a coordinate is editable without touching a pixel, and a
        test pins that stripping only the GPS IFD from an image leaves the score
        unchanged.
- [x] P7.B6 Dark/light CSS vars; WCAG 2.1 AA (keyboard/ARIA/focus/contrast)
      Colour lives in CSS variables with one `.dark` class, so a dark-mode bug
      cannot be a forgotten `dark:` variant on one element. The theme toggle reads
      the resolved theme with `useSyncExternalStore` rather than mirroring it into
      state from an effect, and an inline script in `<head>` applies it before
      first paint — verified to persist across a reload in both directions.
      **Now measured rather than asserted.** axe-core scans the rendered login
      page, the case list, and the case workspace in both themes, and
      `tests/contrast.test.ts` computes every foreground/background pair straight
      from `globals.css`. Both were worth having: **axe-core caught a real failure
      and so did the token test, in the same place.** `--text-tertiary` shipped at
      4.07:1 against the nested surface while a comment in the stylesheet claimed it
      was 4.6:1 — arithmetic nobody had done. It is the token every timestamp,
      version string and secondary label in the app is painted with, so it was
      failing on nearly every screen. Now 4.5:1 minimum, verified two ways.
      The token test also fails if a token is *missing*, because a silently skipped
      pair is how a check reports "clean" over a palette it never looked at.
- [x] P7.B7 Explainability UX (score meaning, threshold, version, why-popovers)
      The score, its threshold and its module version render as one unit, always —
      a score shown alone is the single most misleading thing this UI could do,
      because 0.8 against a 0.25 threshold and 0.8 against a 0.9 are opposite
      conclusions. `explainScore` also distinguishes *near* threshold from *below*
      it: rendering a borderline value as an unqualified pass hides it, which is the
      same class of error as overstating it.
      Per-module plain-language copy is deliberately **not** complete for every
      module and says so when it is missing, rather than falling back to generic
      text — an unexplained analyzer should be visibly unexplained.
- [x] P7.B8 Honesty-clause Tier-A/B disclaimers on results + reports
      This is the part of the app the rest of it exists to protect, so it is one
      pure module (`src/lib/explain.ts`) rather than something each component
      re-implements. `explainJob` is total: there is no input for which it returns
      "show a score" when nothing was measured, so `not_applicable` cannot reach
      the UI as a zero. A refusal renders as a neutral notice, not an error —
      styling it red would train people to skip past it, which is exactly when it
      matters most.
      Calibration facts the analyzer recorded about itself are surfaced
      automatically (`calibrated_on_this_deployment: false`,
      `is_zero_meaningful: false`, single-reference-frame PRNU). **These were
      initially read from the wrong level of the payload** and silently rendered
      nothing, because module facts live under `payload.detail` and the first
      version read the envelope. Found by comparing the UI against a live result.
- [x] P7.B9 Loading/skeleton/empty/error states; optimistic UI; async job progress
**Test cases**
- [x] T7.1 E2E (Playwright, no mocks): login -> create case -> upload -> run
      analysis -> read the result -> survive a reload. The ELA overlay is asserted
      by `naturalWidth > 0`, because a broken image is still "visible" to
      Playwright and only the decode proves it rendered. **Report generation is not
      covered** — that is Phase 8, so this line will be revisited then rather than
      ticked early.
- [x] T7.2 HITL approve/reject as an automated browser test.
      **The obstacle I recorded earlier was not real.** I had it as: parking an action
      needs a model that decides to ask for approval, and the 3B one is inconsistent,
      so making it deterministic means scripting the graph — which "stops the test
      exercising the browser path that broke." That conflates two different jobs.
      *Arranging* the state is setup and is fine to script; *exercising* the behaviour
      is what the test is for. `scripts/seed_parked_run.py` drives the **real**
      `AgentGraph` with a one-shot model so the real `NeedsApproval` branch parks the
      action, and the browser then does the part that actually broke — see the gate
      after a reload, answer it, watch the write land. Nothing about the browser path
      is mocked.
      **Found two real bugs, neither of which a hand-driven check surfaced:**
      - **The gate rendered inside a collapsed row.** An operator returning to a case
        had to go hunting for the question waiting on them. A parked action that has to
        be discovered is one that goes unanswered, so a run awaiting approval now
        auto-expands.
      - **Answering left the question on screen.** The server action returned ok and
        the database had the run past the gate, but the gate stayed visible until a
        *hard* reload. Answering a question and still being shown that same question
        is the worst outcome a HITL control can have: it reads as "it did not work",
        and the operator's next move is to approve it again. `router.refresh()` did
        not clear it, and adding the missing `revalidatePath` — which
        `approveActionAction` was uniquely not calling, while every other mutating
        action in the app did — did not either. The gate now clears on the confirmed
        decision, held in local state, because the decision *is* already recorded
        server-side by then; it is not waiting for a refresh that does not arrive.
      The seeded run also found a **backend bug in `recall_memory`**: it passed a
      Python list where asyncpg needs a vector *string*, so the pgvector branch raised
      `expected str, got list`. It was invisible to the suite *and* to the running
      system at once — every test runs SQLite and takes the working fallback, and this
      deployment's embedding endpoint 404s, so `query_embedding` is always `None` and
      production never reached the broken line either. The seed's stub model returns a
      real embedding, which is the first time anything got there. Fixed, and now
      covered by a test that runs the pgvector branch on real Postgres.
- [x] T7.3 axe-core AA on the rendered pages in both themes; keyboard-only sign-in
      and submission with focus asserted at every step; tab strips with roving
      tabindex; contrast computed from the tokens. **Finding: the overview summary
      rendered a score and a tier badge with no limitation on it** — caught because
      the test asserts the disclaimer on *every* element that shows the version, so
      the summary could not quietly drop it.
- [x] T7.4 Every rendered result carries disclaimer + threshold + model version,
      asserted on **every element that shows the version** rather than on the detail
      card alone — which is what caught the overview summary omitting the
      limitation. Runs against a live analyzer result, so it fails if the API ever
      stops sending a field the UI has no way to invent.
- [x] T7.5 A queued analysis renders a live status region with a progress bar, and
      the result appears on its own when the job finishes — the client polls and
      refreshes rather than the page sitting on a pending request. Polling is bounded
      (60 attempts) so a wedged job does not become a request every two seconds
      forever, and the page stays interactive throughout. **No toast layer:** progress
      appears inline where the work was started, and a single page already covers
      the case. Worth revisiting if a job ever runs that the operator did not start.
- [~] T7.6 Dark/light persists across a reload in both directions (asserted).
      Geospatial view renders real coordinates — 51.50000, -0.11667 with the raw
      51°30'0" 0°7'0" beside it, verified against a generated EXIF fixture. The
      result overview is computed straight from the API payload with no intermediate
      transform, so it cannot disagree with the data. **"MapLibre renders real
      coordinates" is not met as written and will not be** — see P7.B5: a tile
      service would be handed a viewport derived from the evidence. The intent
      behind the line (real coordinates actually drawn) is met; the named tool is
      refused, with the reason recorded.
- [x] T7.7 Every list section distinguishes "could not load" from "loaded and
      empty", and says which. Enforced by the types rather than by discipline: when
      `safe<T>()` returned `null as T` the compiler was silenced and the page crashed
      three components later on an unchecked `.filter`. A missing evidence record's
      bytes render as **410**, not 404 — an integrity incident, worded as one, never
      a broken image icon. A failed overlay, an unreachable API, a declined agent run
      and a missing analyzer description each have their own wording.
**Exit**
- [x] P7.E Full journey E2E, AA-accessible, disclaimers enforced in UI.
      The journey runs in a real browser against the real stack; axe-core passes on the
      rendered pages in both themes; the disclaimer is enforced by an assertion rather
      than by review. Exited now that T7.2 is automated — it was the only thing
      blocking it. The HITL round trip that closes this phase is the one that broke
      twice in the hand-driven phase, and it is the part most worth having as a test.

---

**Bugs found while building signed reports (P8)**

All found by generating a real report from a real case and then trying to verify it,
which is the first moment most of these become visible.

- **`skip_diff=True` silently disabled the verification it was passed for.**
  pyHanko's flag suppresses the document/signature diff — the one check the module
  exists to perform. It was passed because it reads like "don't be strict", and a
  modified document could come back reporting itself intact.
- **`intact` was not sufficient on its own.** Editing the report's content leaves
  `intact` False; corrupting the *signature* leaves `intact` True and only `valid`
  False. Both are now required, because they catch different attacks.
- **A corrupted signature raised instead of being reported.** A byte flipped inside
  the signature value left pyHanko unable to identify the certificate, and it raised
  `SignatureValidationError` — so "this document was tampered with" became a 500. A
  verifier that errors on precisely the documents it exists to catch is worse than
  none, because the failure reads as a fault to retry rather than a finding to
  believe.
- **`load_pkcs12` returns `None` for some unreadable archives** rather than raising,
  which wrapped a `None` signer and failed later at signing time with an attribute
  error on `None` — pointing at the report builder instead of at the key.
- **`Finding` could not be sourced at all**, from either the API or the agent. The
  agent recorded a *count* of results on the case, which says nothing about what its
  finding rests on, and a count cannot be put in a provenance appendix. Fixed with
  `FindingCitation` and a validation rule that refuses rather than drops.
- **`_scoped_case` existed as three private copies** in the domain, agent and
  analysis routers, and a fourth was about to be written for reports. Identical
  behaviour — not yet drifted — but three copies of an authorisation rule is three
  places for one of them to be edited and missed, and the missed one is the one that
  leaks. Now one `scoped_case` in `app/domain/access.py`.
- **The manifest digest rendered across a page break**, its label at the foot of one
  page and its value on the next. A value the report explicitly invites you to
  recompute should not be split. Digests now print as explicit 16-character lines
  inside a keep-together, so a wrap is visible rather than ambiguous.
- **The cover read "1 of the 3 findings … rest … are marked".** A legal document
  that cannot count.
- **`pdftotext` spliced table columns into digests.** The first digest assertion
  flattened all whitespace, which merged the *next column's* text into the middle of a
  64-character hash and reported a correct report as missing its digests. The test was
  wrong, not the report — worth recording because the same mistake would have been
  made again by anyone writing the next assertion.

**Bugs found while building the frontend**

All found by driving the real stack. The first three are backend defects the
green suite could not see; the last two are frontend defects that unit-test-free
UI always has.

- **The `cases` router rate-limited *reads* under a bucket named `mutation`,**
  at 60/min. One case page renders seven reads in parallel, so ordinary navigation
  returned 429 — the UI's normal behaviour, not an attack. Reads and writes now
  have separate budgets (600/min and 60/min), and the write ceiling is asserted
  separately so raising the read number cannot quietly remove it.
- **A missing evidence object returned 500 with a stack trace.** `ObjectStore`
  raised the minio SDK's `S3Error` past the `FileNotFoundError` every caller was
  already handling. Found by viewing a case that held a row whose bytes were never
  in MinIO — a state nothing constructed in tests. Now translated at the storage
  layer (so every caller benefits) and reported as **410**, not 404: the record
  exists and the bytes do not, which is an integrity incident and must not look
  like a mistyped id.
- **The workflow state machine was not exposed to clients.** A UI had to either
  hard-code its own copy — which drifts, and produces a button the API then
  rejects with 409 — or render nothing. `CaseRead` now carries
  `allowed_transitions`, filled in by one `case_to_read` helper because there are
  eight call sites and one that forgets it claims a case is closed.
- **The gunicorn worker timeout was 30s, which kills every agent run.**
  `GET /agent/runs/{id}/stream` is an SSE response whose purpose is to stay open
  while the model works, so any run over 30s had its worker SIGKILLed mid-stream.
  Gunicorn reports that as *"Perhaps out of memory?"* — and the container's cgroup
  recorded zero OOM kills, so the message sends you looking at the wrong thing.
  Raised to 900s and configurable via `WEB_TIMEOUT`. No test could have caught it:
  every backend test drives the graph in-process and never goes through gunicorn.
- **`POST /agent/cases/{id}/runs` ran the whole agent loop inline** despite its docstring
  promising to "return immediately", so it blocked for every LLM round trip before
  answering — 36ms now. The first step and its checkpoint are written by the POST
  and the SSE stream drives the graph, which is where the work belonged.
- **The agent's provenance was never serialised.** Claims, citations and tool calls
  were stored in the checkpoint the whole time and absent from every API response,
  so the only way to check whether a claim was sourced was to query the database.
  That makes the agent's central contract — decline rather than assert what cannot
  be cited — unverifiable by any client, and a UI that renders the prose without
  the sources quietly undoes it. `_run_payload` now carries all four lists.
- **Approving in the browser hung on "Applying…" forever.** `POST /runs/{id}/approve`
  applied the action *and* ran the rest of the agent loop inline, so the click took
  as long as a model run and the button sat disabled the whole time. Split into
  `approve_only`/`reject_only` (apply, checkpoint, return) with the stream
  continuing the run: **37ms** now, and the approved write has demonstrably landed
  before the response — approving is the irreversible half.
- **The approve/reject buttons only existed on the live panel**, so an action parked
  for approval became permanently unactionable the moment the operator reloaded. The
  run waited forever for a person who could no longer see the question. The gate is
  now one component used by both the live console and the run history.
- **An approved-and-applied write was reported as `failed`.** The parked
  `ToolCall`, the `PendingAction.action_id` and the call re-executed on approval
  were three unrelated random ids, so nothing could join them and the audit display
  had no result to find. One identity now: `action_id` *is* the call id, and
  `ToolResult.approved_action_id` records who authorised it — read from the result,
  not from the pending action, because that is cleared as soon as the run continues.
- **A parked action displayed as "failed".** `ok` was joined from the result, and a
  parked call has no result, so waiting rendered as failure. Tool calls are now
  three-state — `ok` / `failed` / `pending` — and "pending" says "awaiting human
  approval; not run yet". Labelling a waiting action as a failure is a false
  statement about the audit trail.
- **No endpoint listed a case's agent runs**, so the console rendered "No run yet"
  on every fresh page load — the agent's report, its citations and its tool-call
  audit all vanished on reload. A run is a durable record; for a forensic tool,
  showing an empty console because the browser that started it is closed is about
  the worst default available. `GET /agent/cases/{id}/runs` now serves every run
  with its provenance, scoped by the same visibility rule as the case.
- **The frontend's agent types were invented, not derived.** They said `id`,
  `module`, `locator` and `quote`; the real payload has `run_id`, `analyzer`, and
  no such things. Typed against the contract as wished-for rather than as served,
  so the console would have rendered nothing. Every one was corrected against a
  live response.
- **`safe<T>()` returned `null as T` in the case page.** The dishonest cast
  silenced the compiler, and three components later a list was `.filter`ed without
  a check and crashed the page — which had passed every check, because there were
  none. `null` and `[]` are different states and now have different types.
- **Calibration warnings silently rendered nothing.** They are read from
  `payload.detail`, and the first version read the envelope, where the keys do not
  exist. No error, no warning — the UI simply appeared confident about a threshold
  nobody validated.

---

## Phase 8 — Reports (signed) + provenance appendix
**Build**
- [x] P8.B1 **ReportLab, not WeasyPrint**, and the reason is the container: WeasyPrint
      needs Cairo, Pango and a stack of system libraries, and this ships as a slim
      Python image. ReportLab is pip-only and Pillow was already a dependency, so a
      report is buildable anywhere the API already runs. PAdES baseline-B via
      **pyHanko**, signed incrementally so the original bytes are preserved verbatim.
      **No unsigned mode**: the endpoint returns 503 with the provisioning command
      rather than emitting a PDF that carries no integrity claim, because an unsigned
      report looks identical to a signed one until someone relies on the difference.
      Baseline-B rather than B-T because a document timestamp needs a trusted
      timestamp authority, which a self-hosted lab has no reason to depend on — and
      the report says so on its face rather than omitting the field.
      `scripts/provision_signer.py` creates the key into a named docker volume, not
      into the image or a bind mount, so it never appears in a layer or on a host.
- [x] P8.B2 Provenance appendix: chain of custody, every agent run, every tool call
      with its argument and result hashes, and separately the calls that only ran
      **because a person approved them** — hashed to bind the record to what was
      approved rather than to a description of it. A finding written by the agent is
      a change someone authorised, and the report shows who authorised it.
      **Required a schema change first.** `Finding` had no link to evidence or
      results; the agent recorded `available_results`, a *count* of results on the
      case, which says nothing about what that particular finding rests on. Added
      `FindingCitation` (migration `0007_reports`), which snapshots module, score,
      threshold, tier, model version and the `not_applicable` refusal at citation
      time — a snapshot rather than a join, because analysis results get re-run and
      superseded, and a report generated later must describe the result the finding
      was drawn from.
- [x] P8.B3 The honesty clause reaches into the typesetting. A finding with no
      citations prints under **NOT SOURCED**, set apart from sourced findings, and is
      never laid out in a way that implies analysis backs it. The cover counts sourced
      and unsourced separately, because "3 findings" is not enough. Every score
      carries its threshold, its analyzer version and that analyzer's own disclaimer.
      A result the analyzer **refused to measure** prints as "could not be measured",
      never as `0.0000`. Tier-C results appear only under *Attached laboratory
      results*, in their own section, never among the findings — an external lab's
      conclusion is not this system's finding, and listing it among them would
      transfer authorship. A structural test asserts no analyzer claims Tier C, since
      the plan cuts those domains as AI analyzers.
- [~] P8.B4 `GET /reports/cases/{id}.pdf` and `POST /reports/verify`. Authz reuses
      the **one** `scoped_case` helper: it existed as three private copies across the
      domain, agent and analysis routers, and a fourth was about to be written. Three
      copies of an authorisation rule is three places for one of them to be edited and
      missed, and the missed one is the one that leaks. Not drifted yet — the argument
      is that they were waiting to.
      Audit records generation with the manifest digest, the signer subject, the
      certificate fingerprint and the signed PDF's own SHA-256, so a report can be
      identified and re-checked later. **No chain-of-custody entry**, deliberately:
      generating a report is a *read*, and a custody entry would claim the evidence
      moved. Reports are generated, never stored — the verify endpoint takes the PDF
      back from the reader, which is what an examiner actually has.
**Test cases**
- [x] T8.1 Measured, not asserted. **82 single-byte flips swept across both signed
      ranges: all 82 detected.** A sample of three offsets passed while a flip
      elsewhere went unnoticed, because the sample landed outside the signed content —
      the first version of this test was measuring the wrong thing.
      Two findings worth recording, both from measuring rather than reasoning:
      - **`skip_diff=True` was silently disabling the whole check.** pyHanko's flag
        suppresses the document/signature diff, which is the one thing this module
        exists to do. Removed; a modified document had been able to come back looking
        intact.
      - **`intact` alone is not sufficient.** Editing the content leaves `intact`
        False; corrupting the *signature* leaves `intact` True and only `valid` False.
        Both are now required.
      - **A corrupted signature used to raise.** A byte flipped inside the signature
        value left pyHanko unable to identify the certificate, and it raised — turning
        "this was tampered with" into a 500. A verifier that errors on exactly the
        documents it exists to catch is worse than none, because the failure reads as
        a fault to retry rather than a finding to believe.
      **Known limitation, measured and recorded in the module docstring:** the
      `/Contents` blob is outside the signed range, so altering the signature's own
      bytes leaves the document digest intact. Detection there depends on pyHanko
      re-verifying the CMS, and on 0.37 it catches 40 of 61 single-byte flips. A trust
      root made no difference. A test re-measures this so a pyHanko upgrade that
      closes it surfaces as a test change rather than passing unnoticed.
- [x] T8.2 The manifest carries every file's recorded SHA-256, and a digest over all
      rows sorted canonically. Over **all** evidence, not only the cited subset: a
      digest over the cited files would let an uncited file be added without the
      manifest changing, so the manifest would attest to an evidence set the case no
      longer has. Order-independent, so two labs exporting the same evidence get the
      same digest. Printed as explicit 16-character lines inside a keep-together —
      the digest originally rendered at the foot of one page and resumed on the next,
      and a value the report invites you to recompute should not be split across a
      page break.
- [x] T8.3 Every finding resolves to its analyzer, its score against its own threshold,
      the evidence file and that file's digest, the analyzer version, and the
      analyzer's own limitation. A citation that does not resolve is **refused, not
      stored**: the API answers 422 and the agent tool gets the rejected ids back so it
      can correct them. Silently dropping one would leave a finding that looks sourced
      and is not. A result belonging to another case is rejected too — the only thing
      stopping a finding on one case citing provenance that exists solely on another.
- [x] T8.4 Tier disclaimers present; Tier-C only as attached lab results
- [~] T8.5 Recorded in the audit chain with the manifest digest, signer subject,
      certificate fingerprint and the signed PDF's SHA-256. **Not** a chain-of-custody
      entry, and that is a decision rather than an omission: a report is a read of the
      case, so a custody entry would assert the evidence moved when it did not. See
      P8.B4.
**Exit**
- [~] P8.E Tamper-evidence is measured (T8.1) and sourcing is enforced (T8.3), but
      one thing is genuinely absent: **long-term validation (PAdES-LT)**. A baseline-B
      signature proves the bytes are unchanged; it does not survive the signing
      certificate's expiry, and a report produced by a lab whose certificate later
      lapses has no archival-timestamp chain to fall back on. That needs a
      document-timestamp authority or an archival timestamp, which is a deployment
      decision rather than a code one — hence partial rather than ticked.

---

## Phase 9 — Expand analyzers + Tier-B/Tier-C handling
**Build**
- [ ] P9.B1 Fingerprint — NIST NBIS. **Dropped deliberately.** Spoof detection (PAD)
      and individualization are different techniques; the only reachable fingerprint
      dataset is CC BY-NC-ND-4.0, whose NoDerivatives clause forbids the derived golden
      set; and NBIS itself is unavailable here (no pip package, no apt package,
      libfprint's GitHub 404s from this network — it is C). Vendoring libfprint's
      sources into the image is a real image-size and supply-chain cost nobody has
      agreed to. Recorded as dropped rather than left quietly open.
- [ ] P9.B2 Speaker — SpeechBrain ECAPA-TDNN
- [ ] P9.B3 Deepfake — Xception/EfficientNet ONNX
- [ ] P9.B4 Gunshot audio — YAMNet/PANNs
- [ ] P9.B5 Handwriting/document similarity
- [x] P9.B6 Text authorship — `text-authorship`, Tier B. **Not DistilBERT, and the
      measurement is the reason.** The plan named a DistilBERT model; tried first as the
      obvious reading and it does not work. Two things were measured before anything
      was written:
      1. **A sentence-embedding model is the wrong tool.** `all-MiniLM-L6-v2` over
         public-domain prose: same-author 0.12–0.49, different-author 0.04–0.48 —
         the ranges overlap almost entirely. It is trained to score *what a passage is
         about*, so it measures topic, and a score from it would look like authorship
         attribution while measuring something else. That is the one substitution this
         project refuses.
      2. **Function-word stylometry is the right tool and still does not separate.**
         Burrows' function-word list plus punctuation frequencies, on the committed
         fixtures (three public-domain authors, two ~6000-word passages each):
         same-author **0.9339–0.9882**, different-author **0.8865–0.9574** — an
         overlap of **0.0235**. An Austen/Melville pair scores higher than one of the
         same-author pairs.
      **So the threshold is 0.94 and does not separate, and that is recorded rather than
      tuned away.** Moving the number until the fixtures divide would have produced a
      green test and a dishonest threshold. A 20 000-word probe *does* separate cleanly
      (same 0.990 vs worst different 0.964) — deliberately not adopted, because fixing
      a fixture size at the point where it makes the technique look good is fitting the
      evidence to the result. Bigger fixtures are the honest next step, at ~100 KB each.
      **No weights.** Pure numpy, fully deterministic, runs unchanged on the
      constrained-host profile. This also dropped a 90 MB ONNX model and a tokenizer
      dependency that would have been pinned for a module with nothing to compute.
      Refuses short text, a missing reference, undecodable bytes, and two identical
      documents (cosine 1.0 by definition is a tautology, not authorship evidence).
      The reference is a second **evidence item** via `reference_evidence_id`, reusing
      the executor's existing case-scoped, hash-verified resolution — the same path
      `face-compare` and `image-prnu` use.
      **Three defects found by building it, none by reading it:**
      1. **The CLI gate rejected every fetch.** `check_gated` compared the flag's
         *string* against `False` and `"auto"`, so the documented `--gated false`
         was treated as gated and refused everything. The unit tests all passed
         because they called the function with a bool — the path a user actually
         takes was never exercised. Now covered by a test that walks the string forms.
      2. **A silent zero.** The first passage derivation rejected any block containing
         a newline and found **0 usable paragraphs out of 2696** — because Gutenberg
         hard-wraps prose at ~70 columns. An empty result looks like "nothing to cut"
         rather than "the filter is wrong", and it raised `IndexError` three frames
         later instead of saying so.
      3. **The test duplicated the code under test.** `fetch_all` gained a filename
         suffix; the refetch test still spelled out the old path by hand, so it went on
         pre-creating a file the code no longer looked at — its setup kept passing
         while the behaviour it exists to test stopped being exercised. The rule now
         has one owner (`fixture_path`) and the test asks for it.
      Pattern worth keeping: in all three the test suite was green and the code was
      wrong, and two of the three were only visible by running the tool.
- [x] P9.B7 Geospatial correlation. `geo-correlate`, Tier B, pure algorithm.
      **No Shapely, no PostGIS.** The plan named both. What this actually needs is a
      distance between two points on a sphere — haversine over `math` — and the
      alternative was a compiled geometry dependency in the image for one function.
      PostGIS earns its place when there are *areas* to test against (is this point
      inside this jurisdiction), and nothing asks that yet. Add it when something does.
      Reads GPS from the evidence's own EXIF and measures it against places the
      caller supplies. Targets arrive as `compare_to` data rather than evidence ids
      on purpose: a module may not read the object store, so the caller resolves ids
      to coordinates, and a module cannot become a way to reach another case's data.
      **Not a manipulation signal, and the payload says so.** A photograph's GPS is a
      number in a file anyone can write; phones reset it, inherit a stale fix, or
      record a network position. So the score is a distance, the threshold is a
      co-location radius, and the disclaimer states that a close match means the
      coordinates agree and nothing more. Asserted, so it cannot be quietly dropped.
      Refuses rather than scoring when there is no GPS or no comparison target —
      a `0.0` here would render as "at the origin", which is a claim about the world
      that nobody measured. Malformed targets are distinguished from absent ones,
      because they have different fixes.
      **Two bugs found by running it rather than by reading it**, both in code that
      looked right: the longitude hemisphere was written as N/S instead of E/W,
      which put western longitudes on the far side of the planet — a fixture that
      looked correct and measured 20,000 km from where it claimed to be; and the
      module read the raw GPS IFD instead of calling the existing `gps_to_decimal`,
      so it reported *every* image as having no GPS — which reads as a correct
      refusal rather than as a bug.
      **Three of my own pinned values were guesses and all three were wrong** (110 m
      separation, the Sydney–New York distance, a disclaimer substring). Replaced with
      measured ones. The geometry is asserted against independently-known distances
      first — 1° of latitude, London–New York, antipodes — because a pinned number
      proves only that the code still produces what it produced, not that the number
      is a distance. A haversine with its arguments transposed is stable,
      reproducible, and wrong.
- [x] P9.B8 Tier B repositioned as visual similarity / triage, with the label
      **served from the backend rather than written in the interface.**
      The plan lists seven trace-evidence techniques (bloodstain, fiber, glass,
      paint, soil, hair, iris). Most of those are *laboratory measurements* —
      glass refractive index, soil microscopy — which is Tier-C reasoning, not
      Tier-B: a computed similarity there would be exactly the plausible-looking
      number this project refuses. Fiber/trace colour and texture comparison from
      two images is genuinely computable and genuinely a visual-similarity triage
      signal, so that is the one worth having, and it is the same shape as the
      PRNU module already shipped. **Not built this pass**: the labelling defect
      found underneath was the real work here, and it applied to every existing
      Tier-B surface.
      **Found a live contradiction between two copies of a safety label.**
      `frontend/src/lib/explain.ts` carried its own tier wording and had drifted
      into saying the *opposite* of the backend on two of three tiers:
      - Tier B was "a corroborating signal" — which is precisely how a
        visual-similarity score gets read as a determination, while the backend
        said "NOT an identity match".
      - Tier C was "unvalidated, research value only", when Tier C is a deliberate
        refusal to score at all.
      Fixed at the owner: `TIER_MEANING` in `types.py`, served at `/tiers`, with
      the interface asking rather than asserting. `explainTier` now has **no
      fallback text** — an absent entry renders nothing rather than something
      plausible and wrong.
      **Found a second, worse one behind it.** The capability panel called
      `explainTier("A")` for *every* module, in an `sr-only` span, so it announced
      Tier A to screen readers for all six analyzers when five are Tier B. The one
      fact a reader opens that panel for, stated wrongly for most of the list.
      `Capability` now carries each module's real tier.
      **And a third instance of the recurring bug class.** `/capabilities`
      re-serialised each field by hand, which is why `tier` *silently failed to
      appear* when added — the field existed, the response simply did not carry it.
      Now serialised from the dataclass. A field-by-field projection is a
      duplicate that must be updated by hand and fails silently when it is not,
      the same shape as the three copies of `scoped_case`.
- [x] P9.B9 Tier-C as lab-only workflows (DNA, tox, pollen, insect, odor,
      explosive/arson). Not "scored conservatively" — **there is no scoring path at
      all**, and that is the design goal rather than a limitation of it.
      `app/analysis/lab_only.py` is a catalog of seven disciplines, each carrying
      what it is, why there is no score, and what to do instead. The refusal is
      raised in the router **before a job row exists**, so a Tier-C attempt leaves no
      job, no worker message and no result — asserted directly, because a leftover row
      would appear in the case's analysis listing looking like real work.
      **Deliberately not `JobStatus.UNSUPPORTED`.** That status means *this host*
      cannot run a real module and provisioning the weights would fix it. Tier C
      means no host ever can, so filing it under `unsupported` would promise a fix
      that does not exist and send an operator looking for one.
      A test asserts no Tier-C technique is ever in the runnable registry, which is
      the property in its strongest form — the router's refusal is then one check,
      not the only thing standing between the catalog and a score.
      Each entry names an **alternative**. Someone who has just uploaded a blood
      sample and been told no reads these fields, and an entry with no way forward is
      indistinguishable from the platform being broken. The wording is also asserted
      never to read as temporary — "not currently supported" is a promise this
      platform does not make.
      Served at `/lab-techniques`, deliberately *not* inside `/modules`: a catalog
      entry with no scoring path is not an analyzer, and listing it beside runnable
      modules would imply it can be queued.
      Refusals are audited. Nobody scored a DNA profile, but somebody asked for one
      and was told no — and with no job row written, that audit entry is the only
      trace left.
**Test cases**

**B1–B6 researched, not assumed. What was actually checked, and what came back.**

I searched Hugging Face and the canonical upstream sources for a labelled fixture for
each of the six. Results, so the next person does not repeat this:

| Need | Best candidate found | Verdict |
|---|---|---|
| Gunshot audio (B4) | `ashraq/esc50`, 4.2k downloads, ungated | **No gunshot class.** All 50 ESC-50 classes fetched and listed; the closest are `chainsaw`, `hand_saw`, `drilling` is absent too. ESC-50 cannot serve B4. |
| Fingerprints (B1) | `UniDataPro/biometric-fingerprint-spoofing`, CC BY-NC-**ND**-4.0 | Two disqualifiers. It is **spoof detection (PAD)**, not individualization — a different problem from NBIS matching, so a golden test on it would not test what B1 asks. And **ND** forbids derivatives, which a derived golden fixture set is. |
| Fingerprints (B1, alt) | `ArwaAbdul/Fingerprint_split_90_10` | No licence stated at all. Not usable for a project that pins licensing. |
| Speaker pairs (B2) | `DynamicSuperbPrivate/SpeakerVerification_Voxceleb1Train` | VoxCeleb is research-only with a signed agreement; the HF mirror is not the licence. |
| Deepfake (B3) | `nuriachandra/Deepfake-Eval-2024`, CC BY-SA-4.0 | **Retracted — it is `gated: manual`.** I recommended this one earlier on the strength of its licence alone and was wrong. It requires institutional email, institution, and attested prior work in deepfake detection; its terms restrict use to evaluation. Every *ungated* deepfake set found states **no licence at all**. |
| Handwriting (B5) | `1aurent/individuality-of-handwriting` | Licence tag reads `license:unknown`. |
| Authorship (B6) | `manu/eli5_authorship_attribution` | Only 5 downloads, no licence stated, and it is Reddit/ELI5 authorship — not the forensic authorship the plan means. |

**The pattern is consistent and it is the actual blocker.** The models exist as
pinnable ONNX — Deep-Fake-Detector-v2, CommunityForensics-DeepfakeDet-ViT, YAMNet,
ECAPA-TDNN are all on the Hub. What does not exist is a *labelled forensic fixture
set* that is simultaneously (a) fetchable in a standard layout, (b) licensed for the
task, and (c) licensed permissively enough to derive golden tests from.

This is a worse answer than "blocked" — it is a concrete, checkable statement of what
each blocker is, so the next attempt starts from evidence rather than from a guess.

**Also verified, still true:** NBIS itself is unavailable here. No pip package
(`pynbis`/`nbispython` do not exist), no apt package on this image, and libfprint's
GitHub returns 404 from this network. It is C.

**And the standing argument holds regardless.** A module tested against synthetic
fixtures tests the plumbing, not the technique: a deepfake detector handed a synthetic
gradient returns a number, and a golden test pinning it proves the ONNX graph is wired
up — which is worth having, and is not what T9.1 asks for. Pinning it would let a
module with no working weights, or weights for the wrong task, pass the phase's exit
criterion. That is precisely the simulation this project exists to refuse.

**The two decisions, made.**

- **ShareAlike is refused for fixtures**, despite this repository having no `LICENSE`
  file and so nothing for SA to conflict with today. Fixtures are copied *into* the
  repo, so the obligation would attach silently to whoever adds a licence later,
  binding them to a term they never chose. **There is still no `LICENSE` file**, which
  is a real gap for a project intended to be open source.
- **B1 (NBIS fingerprint) is dropped rather than left open.** Spoof detection and
  individualization are different techniques, the only reachable fingerprint set is
  ND-licensed, and NBIS itself is unavailable here (no pip package, no apt package,
  libfprint 404s). Vendoring libfprint's C sources into the image is a real image-size
  and supply-chain cost that nobody has agreed to. Leaving it ticked-but-empty would
  have been worse than saying so.

**What was built instead: `scripts/fetch_fixtures.py`, a licence gate.**

The research showed the blocker is *licensing*, not downloading — so the licensing
question is now answered in code rather than in prose. It refuses, with a reason:

- **gated** sources (`nuriachandra/Deepfake-Eval-2024` among them), because a gate is a
  human accepting terms and fetching around it puts those terms into the repository;
- **unstated** licences, because unstated is not permission and that is the common case;
- **ND**, because a golden fixture set is a derivative;
- **SA**, per the decision above;
- everything not on a closed allowlist, so `other` and `unknown` cannot slip through.

Gating happens **before any network call**, asserted by a test whose fetch function
*raises* if invoked — otherwise the gate would be advisory and would already have
pulled bytes from a source whose terms say it should not have. Every pin records URL,
SHA-256, licence and role in a manifest, so the provenance is readable at the point of
use rather than only in the git history. This mirrors `fetch_models.sh`.

Fourteen tests, and the refusals are the tests: a gate never seen refusing is a gate
that might not work — the same argument as the CI security scanners.

- [x] T9.1 Deterministic golden tests for the new analyzers. `text-authorship`
      (`tests/test_golden_authorship.py`, 13 tests) on six pinned public-domain
      passages. **The load-bearing test asserts a negative result**: same-author
      similarity is *not* required to exceed the worst different-author pair, and the
      measured ranges are pinned to five decimals. If a future change makes them
      separate, that test fails and says to update the module and the threshold — so
      the separation cannot appear quietly in a golden file and be read as evidence
      the module works. The exact ranges also carry the pair counts (3 same-author,
      12 different-author), because a run that stopped comparing everything would
      otherwise still pass the range assertions.
      The determinism test is worth noting: there are no weights, so this module does
      not need the drift guard every other analyzer here needs — asserted rather than
      assumed. And the fixture is checked for length directly, because a shortened
      fixture would make the module refuse while the refusal tests kept passing, so
      every pinned number would be describing a different experiment.
- [ ] T9.2 NBIS matching high / non-matching low on fixtures — blocked, see P9.B1
- [ ] T9.3 Speaker same>threshold / different<threshold — blocked, see P9.B2
- [x] T9.4 Tier-B always labeled "not an identity match", in the disclaimer, the
      tier vocabulary, the capabilities list and the report. Nine tests, and the
      part worth having is the **reverse** checks: Tier A must *not* claim to refuse
      identity, and the capabilities list must contain both tiers, so a test cannot
      pass on a single-valued field. `explainTier` returning `null` rather than a
      default is itself load-bearing — the label must not be invented anywhere.
- [x] T9.5 Tier-C: no scoring path, asserted rather than assumed. Nine tests,
      because the property under test is *negative* — nothing here proves Tier C
      works, since working is exactly what it must not do. Every technique in the
      catalog is refused the same way (parameterised, because the failure is a
      technique being quietly forgotten); no job or result row survives a refusal;
      the refusal carries the discipline and the alternative; the catalog never
      surfaces a module-shaped field an interface could render as a score.
- [x] T9.6 Plugin isolation: registering a module is the *only* step required.
      The test defines a complete analyzer in the test file — never heard of by the
      codebase — and asserts it reaches all four places a hardcoded list could
      hiding in: `/modules`, `/capabilities` (including the tier the interface now
      reads rather than asserts), job submission, and a real run that stores a
      result. A module that exists only in test code and is fully functional
      everywhere is the strongest available evidence the extension point is real.
      Driven through the **real upload endpoint**, not by inserting evidence rows:
      seeding rows leaves the object store empty, the run fails on a missing object,
      and the test then says nothing about the thing it is checking.
      `not_applicable` is asserted too — a plugin that could only ever succeed would
      make the pipeline look more capable than it is, and the refusal path is what
      keeps "we could not measure this" distinct from "we measured zero".
      The negative half is what makes the positive mean anything: registering must
      leave every built-in untouched, or a catalogue that mutated on registration
      would make every golden test's module list a function of test order. And a
      duplicate name is refused, because at that point two analyzers are claiming one
      identity and whichever loaded second would silently win.
      **This test would have caught both bugs from the previous commit** — the
      hand-written capability list and the hand-written field serialisation — and
      neither was visible from the tests that existed.
**Exit**
- [~] P9.E **Half met, and the half that is not is named.** The catalogue is complete
      for everything this platform scores and **honestly tiered** — Tier B's label
      served from the backend so the interface cannot contradict it, Tier C a refusal
      with no scoring path, every AI module deterministic and digest-pinned with
      golden tests against real bytes. **B6 shipped** (`text-authorship`, pure stylometry,
      no weights) and its threshold is recorded as **not separating** its fixtures
      rather than tuned until it did. The remaining half is five analyzers (B1–B5) and
      their two test cases, all blocked on labelled fixtures, recorded with reasons at
      P9.B1–B5.
      **Why five analyzers were not written anyway**, since "blocked on fixtures" invites
      the reading that the code was easy and the data was missing. It was not: without
      fixtures there is no way to know whether any of these techniques *works on the
      evidence*, so each module would ship a threshold that nothing establishes, a
      payload that could only say "uncalibrated", and a number an examiner would read as
      a finding. Five of those is worse than none, because the interface would offer
      them and the disclaimer is not what a tired person at 3am reads. The blocker is
      external data with no permissively-licensed source; the reasoning per technique is
      at P9.B1–B5.

---

## Phase 10 — Hardening, observability, ops, Android dev profile
**Build**
- [~] P10.B1 Observability — **Prometheus + alerting done; tracing done; Loki,
      Grafana dashboards and Sentry not done.**
      Prometheus scraping, the metric set, and alert rules are complete (T10.2), and
      the parts that make them *believable* are tested. Loki and Prometheus config
      exist but nothing renders a Loki dashboard.
      **Tracing is built — spans stored in Postgres, no SDK and no collector** (T10.1).
      The previous note on this item claimed tracing needed the OTel SDK added to a
      slim image and a collector in CI to assert parent/child against. **That was
      wrong, and it is worth recording why rather than quietly replacing it.** Both
      requirements follow from assuming spans are *exported*. Spans can be *stored*,
      and this system already has a store that is backed up, hash-verified, case-scoped
      and air-gapped — so the image is unchanged, the P10.B4 egress allowlist stays
      meaningful, and T10.1 is asserted against the database rather than against
      infrastructure CI would have to run. **A trace stored with its case can be joined
      to it, correlated with an audit entry and restored from a backup; a trace
      shipped to a collector can do none of those.** The real problem was never the SDK,
      it was the queue boundary, and that is solved and tested — see T10.1.
      What tracing still does not do: sample, aggregate, or render a flame graph. It
      answers one question well — what happened to this job, in what order, and how long
      each part took — which is the question a stuck analysis actually raises.
      Sentry is deliberately absent: it is a third-party SaaS endpoint, which the
      egress allowlist added in P10.B4 would by default refuse. For an air-gapped
      deployment it is the wrong tool, and self-hosted error tracking is the honest
      answer if it is ever wanted.
- [x] P10.B2 Backups — pg_dump + cryptographic verification + verified restore.
      `scripts/backup.py` with `create` / `verify` / `restore`. What is finished and
      tested: the dump, the manifest, and **cryptographic verification**.
      Evidence is content-addressed (the object key *is* the SHA-256, see
      `app/domain/storage.py`), so verification is a digest comparison rather than a
      structural one. For every row the database records, `verify` hashes the backed-up
      copy and compares it to the recorded digest — a truncated object, a partial
      write and a silently-substituted file all fail, with nothing to interpret.
      Digests come from the *database*, not the backup's own inventory, so a backup
      that is internally consistent but missing evidence is caught too.
      `create` refuses to write evidence whose bytes disagree with its recorded
      digest, so a corrupt store fails at backup time — where it is diagnosable —
      rather than producing an archive that fails its own verification later, in a
      directory named by a timestamp.
      `restore` writes to a named target and has **no in-place path and no
      `--force`**: a restore tool that can overwrite the system it protects will
      eventually be aimed at it by someone in a hurry.
      **Eight tests green.** They cover a missing object, a same-length corrupted
      object (the case a size check misses), a missing database dump, refusal to
      restore an unverified backup, refusal to back up mismatched evidence, and
      chunked hashing of a multi-megabyte file. Every one asserts the *failure
      message names the evidence record*, because "the backup is incomplete" is not
      actionable at 3am.
      **Also found, and it changes the design: `pg_restore`'s exit code cannot be
      trusted.** A PG18 `pg_dump` client against a PG16 server emits
      `SET transaction_timeout = 0`, which PG16 does not know — so `pg_restore`
      exits non-zero having *applied the archive*. A restore tool that treats that
      exit code as the answer to "did the restore work" fails on every archive it just
      wrote. So the archive is applied and then **verified**: every digest the manifest
      records must be readable out of the restored database. That is a stronger
      guarantee than any exit code, because it is a statement about the data rather
      than about the tool's mood. That verification caught a silent restore failure on
      its first run, which is the argument for building it that way — see T10.3.
      Still to do: MinIO-side replication, and PITR documentation.
- [x] P10.B3 Graceful shutdown + readiness/liveness. `app/drain.py`, SIGTERM handler,
      `SHUTDOWN_GRACE_SECONDS`.
      **The order is the feature.** Readiness drops *first*, because `/readyz` is the
      load balancer's only signal — if it keeps answering until the socket dies, the
      balancer routes new requests here for the whole grace period and cuts every one
      off. Then the grace period, then connections are released. Tearing down first
      would mean answering requests against a disposed pool: a 500 rather than a
      redirect.
      **New work refused, in-flight work continues, and the two are separated on
      purpose.** A new agent run or analysis gets a 503 naming the retry — refusing
      costs one retry, while starting work that will be killed costs the caller the
      work and leaves a half-written row behind. Reads keep serving throughout: a read
      costs nothing and finishes immediately, so refusing it only makes a routine
      deploy look like an incident.
      **The drain reports itself explicitly** (`"draining": true`) and reports *no*
      dependency failures, because nothing has been torn down yet and a dashboard
      showing Postgres as failed during a deploy sends somebody to the wrong system.
      `/healthz` deliberately stays 200: liveness asks "is this worth restarting", and
      a draining process is not broken — restarting it abandons the in-flight report
      the drain exists to finish, and the orchestrator then watches it die again.
      The signal handler **chains** uvicorn's rather than replacing it: replacing would
      stop uvicorn shutting down and leave the container hanging until SIGKILL, which
      is the opposite of graceful and invisible until the grace period expires.
      **Grace defaults to zero, deliberately.** The right value is entirely a function
      of how long this deployment's slowest operation takes, and a default either
      truncates reports or stalls every rolling deploy. It has to be set on purpose.
      **Horizontal scaling is not built.** The drain flag is per-process, which is
      correct under a balancer (each pod gets its own SIGTERM and must answer for
      itself — a shared flag would have one process advertising itself as ready while
      another drains), but nothing here drains the Postgres connection pool against a
      load balancer or runs multiple replicas under compose. Recorded as not done
      rather than implied.
- [x] P10.B4 Egress control. `app/agent/egress.py` + `EGRESS_ALLOWED_HOSTS`.
      **The surface is one call, and the docstring says so rather than implying
      coverage.** `app/agent/llm.py` holds the only outbound HTTP in `app/`; Postgres
      and MinIO are on the deployment's own network. A test **AST-scans `app/`** and
      fails if a second outbound client appears — because the moment one does, this
      control silently stops covering anything while still reporting that the
      deployment is air-gapped. A grep for `http` would have found comments.
      **An allowlist of hosts, not a mode flag.** One concept rather than a switch
      and a list that can disagree, and *empty means no public host* — the air-gapped
      default. Defaulting to permissive makes the control opt-in, and a control that
      must be switched on before it does anything is one most deployments never
      switch on. Local and container-network endpoints (`localhost`, `127.0.0.1`,
      `ollama`, `host.docker.internal`) are always permitted: running a local model is
      what an air-gapped deployment does, and requiring it on the list would teach
      operators the control is noise.
      `.local` is deliberately **not** treated as local — mDNS names resolve on a LAN,
      and one can be claimed by any host on it. A permitted-looking name that any
      machine on the network can take is exactly what this control exists to stop.
      Schemes can be pinned (`https://api.example.com` permits TLS only), because a
      permitted hostname reached over plaintext is a permitted hostname anyone on the
      path can read.
      Checked when the client is built, inside `resolve_model` **before the provider
      branch** — asserted structurally via AST, since a check inside the cloud branch
      would be routed around by any other provider spelling. The refusal names the
      host, the setting, and the reason, because "connection refused" at 3am sends
      the operator to the wrong file.
      **Two holes found in my own policy by the tests:**
      - **`host.docker.internal` was blocked.** Dotted, so not covered by the
        single-label rule — and it is exactly the address an operator reaches for when
        running a local model on the host. A hole in a control whose whole job is to
        have none.
      - **Malformed input was classified as local.** Every dotless host is a
        container-network name, so `"not a url at all"` was permitted as local. Hosts
        are now validated as hostnames *before* classification; otherwise the control's
        classification can be talked past by a typo.
- [~] P10.B5 Ops docs done; **load tests and a penetration test not done.**
      **Threat model brought up to date, and it had drifted badly.** It was written
      for Phase 0–3 and still said the agent, analyzers, reports and frontend "need
      their own pass" — all of which shipped without it. A security document describing
      a system smaller than the one deployed is *worse* than none, because it is read
      as current. That is the same failure mode as the stale capability list, and it is
      now called out in the document itself so the next drift is recognisable.
      Phases 5–10 are now covered: signed reports, pinned model artifacts, the analyzer
      registry's isolation, queue provenance, the drain ordering, egress, backups, and
      the security-gate verification. Added a section stating the agent's authority
      plainly (cannot write without a person, cannot cite what does not exist, cannot
      widen its own scope, cannot reach the network) — followed by the statement that
      the *residual* risk is not a jailbreak but a correctly-scoped, correctly-cited
      agent whose conclusion a human still reads as a finding, which is what the
      tiering is for and which no prompt hardening substitutes for.
      **Open items are now written down**, because an open item nobody recorded is an
      open item nobody is working on: audit tail truncation, per-IP-only rate limiting,
      the absent `analysis_jobs_total` samples, no pen-test, and no PITR.
      Admin guide now covers egress, backups, draining, the constrained-host profile
      and alerting — including the three shutdown behaviours that surprise people
      (`/healthz` stays 200, reads keep working, `dependencies` is empty).
      **Load check done, as `scripts/load_check.py` — no Locust.** It is a
      browser-oriented tool that brings a web UI, a worker fleet and a web server to
      answer a question `httpx` answers in a hundred lines, and more importantly it
      reports requests-per-second, which is not the property worth checking here.
      **Latency is printed, never asserted**, because no SLO has been chosen and a
      hardcoded one would make this the first file in the repo to disagree with
      reality. What *is* asserted are the absolute invariants that hold at any load:
      every job reaches a terminal state, none is claimed twice, and `/readyz` stays
      ready throughout.
      **Measured against the live stack: 30 jobs across 6 cases, 30 `done`, 0 lost,
      0 readiness failures.** p50 35 ms / p95 56 ms submit latency, reported only.
      **Two bugs the first run found — both in the check, and both the dangerous kind
      because they made a weak run look like a strong one:**
      - **It reported 8 phantom failures.** It polled each of 64 job ids
        individually, exceeded the read rate limit, and treated a 429 as evidence
        about the job. Only one job in the database was actually stuck — and it was
        not one of these. It now polls **per case** and tracks *unknown* (could not
        read) separately from *lost*, because a check that cannot tell "the job failed"
        from "I was not allowed to look" eventually reports a healthy system as broken
        and gets ignored.
      - **It reported "30 jobs submitted" while queuing 6.** `submit_job` is
        idempotent on (evidence hash, module, params), so submitting the same evidence
        five times creates *one* job and four no-op reuses. It was counting HTTP 201s
        rather than jobs. Each submission now uploads distinct bytes, so every one is
        real work, and the reuse count is reported separately. **The idempotency
        feature defeated the load check's own accounting** — which is worth knowing
        about the feature too.
      **Not done: penetration test.** Bandit, Semgrep, pip-audit, Trivy and gitleaks
      run in CI and are themselves proven to fail on planted problems — that is
      evidence the scanners work, not evidence the application has no findings.
- [x] P10.B6 Constrained-host profile (`android-dev`): CPU-only ONNX, capped local
      model, GPU modules degraded cleanly.
      **Found a profile that could not run the platform.** `worker` was in `core`
      only, so `android-dev` started a database and an LLM with *nothing to run an
      analysis*. `/capabilities` would have reported the analyzers available, jobs
      would have queued and never been claimed, and the first symptom would be a case
      that quietly never finished. A dev profile that cannot run the product is worse
      than no dev profile, and it was invisible because every capability test passed.
      Also made the api→worker image link **explicit**: `api` built while the workers
      referenced `forensight-api` by name, which compose resolved implicitly from the
      service name. It worked, and a rename would have produced workers pointing at an
      image nothing builds.
      Ollama is capped to one resident model and one parallel request, because a
      phone-sized host that keeps two resident swaps or OOMs — and the symptom is an
      agent failing intermittently rather than a clear refusal.
      **CPU-only is a supported deployment, not a degraded one.** Every analyzer here
      is ONNX, so the CPU path runs the *same pinned weights* through onnxruntime
      rather than a smaller substitute — a substitution would be a simulation, which
      is what this project refuses everywhere else. Asserted non-empty, because the
      prior hand-written capability list advertised three GPU modules no analyzer
      implemented while omitting the five that do.
**Test cases**
- [x] T10.1 Trace spans API → worker with correct parent/child. **17 tests**, 4 of
      them against live Postgres.
      **The earlier "not written" assessment was wrong, and worth recording why.** It
      said tracing "needs the OTel SDK in a slim image and a collector in CI to assert
      parent/child against". Both follow from assuming spans are *exported*. Spans can
      be **stored**, and this system already has a store that is backed up (P10.B2),
      hash-verified, case-scoped and air-gapped — so there is **no SDK and no
      collector**, the image is unchanged, and T10.1 is asserted against the database.
      A trace that lives with its case can be joined to it, correlated with an audit
      entry and restored from a backup; a trace shipped to a collector can do none of
      those.
      W3C Trace Context is written out by hand — four hex fields in a header, not a
      dependency — and parsed **strictly**. A malformed or all-zero `traceparent` is
      *ignored*, never guessed at, because a wrong parent link silently attaches work
      to the wrong trace, which is worse than losing it.
      **The queue boundary is the real problem and it is asserted directly.** The job id
      is the only thing crossing from the API process to the worker, so naive
      instrumentation yields two root spans that *look* like one continuous trace while
      measuring nothing across the boundary. The `traceparent` rides in the Dramatiq
      message options and is re-parented on delivery, and
      `test_the_worker_span_is_a_child_of_the_api_span_across_the_queue` asserts the
      worker's `parent_span_id` is the API's `span_id` **on live Postgres** — it cannot
      be asserted in-process, because any single-process test flattens the boundary back
      into one and then proves nothing.
      **Two real bugs, both found by running it:**
      - **`perf_counter_ns` was used as the span timestamp.** It is right for measuring
        a duration and useless for ordering rows: its origin is arbitrary and differs
        *per process*, so the API's span and the worker's span sorted by two unrelated
        epochs. Caught by a test asserting a non-negative duration, which came out
        negative because the test passed its own `ended_ns`. Wall-clock
        `time.time_ns()` for ordering, monotonic for duration.
      - **A `uuid.UUID` in a span attribute made the span silently unwritable.**
        `jsonb` rejects it at flush time — and `record` deliberately swallows its own
        errors so tracing cannot break a case submission, so the combination meant
        tracing *appeared to work and recorded nothing*. Every SQLite test passed;
        SQLite's JSON type is more forgiving. This is the same failure the Phase 6 live
        -Postgres tests were added for, arriving again in a new column. Attributes are
        now coerced explicitly, with a test that round-trips them through `json.dumps`.
      Tracing **can never break an operation** — the swallow is the point, and commented
      as such, because a bare `except: pass` elsewhere in this codebase is a bug. The
      one exception is a `required=True` mode that raises, which is what lets a test
      assert on stored rows rather than on a mock recording a call.
      `GET /traces/{trace_id}?case_id=` nests spans by parent link. **`case_id` is
      required**: a trace readable by id alone would let any signed-in user time other
      people's investigations by guessing, so a trace is only readable in the context of
      a case the caller may open, and every span in it must belong to that case. Health
      and metrics paths are excluded — they fire constantly and would bury every genuine
      trace in rows nobody will read. Spans cascade-delete with their case.
- [x] T10.2 Prometheus scrapes this service; alert rules that can actually fire.
      `infra/observability/alerts.yml`, wired into `prometheus.yml` via `rule_files` —
      a rule file nothing loads is a file that does nothing, which is exactly the shape
      of "we have alerting" in a repository.
      **Fifteen tests, and they assert content rather than availability.** A scraper
      returning 200 with nothing in it looks exactly like a healthy service, so a
      counter that counts requests must be shown having counted the requests made.
      **The alert rules are the part worth having, because that failure mode is
      silent.** A rule written against a metric name or label that was later renamed
      evaluates to an empty vector forever: it parses, the dashboard renders, nothing
      announces it, and the board stays green through the outage it was written for.
      So every referenced metric is checked against what this build actually emits,
      every `by (...)` is checked against labels that exist, and `promtool` validates
      the rules when the binary is present — **skipped rather than faked when it is
      not**, since a rule promtool rejects will not load and pretending it was checked
      is the false assurance this suite exists to prevent.
      **`ForenSightAPIDown` (`up == 0`) carries the observability.** Every other rule
      on this service is blind while the target is down, which is precisely when they
      are needed.
      Two decisions the tests forced:
      - **The failure alert counts only `outcome="failed"`.** `unsupported` and
        `not_applicable` are a module declining an input it cannot honestly score —
        exactly what it should do — and paging on them trains people to ignore pages.
        Asserted, because collapsing them into "failure" is the obvious simplification
        and a wrong one.
      - **A scrape is counted, and no rule alerts on it.** The first draft of that test
        asserted `/metrics` never appears in its own output; it does, and that was the
        *test* being wrong. Omitting it would mean the request counter silently
        disagreed with reality. The requirement is on the rules instead.
      **Known gap, asserted rather than hidden:** `forensight_analysis_jobs_total` is a
      *labelled* counter, and `prometheus_client` emits no samples for one until
      `.labels()` is called — so it is absent from `/metrics` on a deployment that has
      analysed nothing, and the rule watching it sits on an empty vector. Not fixable in
      the metric, because pre-initialising every (module, outcome) pair means
      enumerating modules plugins register at runtime. The honest options are the
      `absent()` variant or accepting the rule only exists once the first job runs.
      **Grafana is not verified** — no dashboard is asserted against a rendering
      Prometheus, and this only covers Prometheus and the rules.
- [x] T10.3 Backup → restore reproduces DB + evidence; hashes verify. Nine tests.
      **The round trip runs end to end**: a real `pg_dump`, a real `pg_restore` into a
      real second database, and every recorded digest recomputed from the restored
      bytes — checked against digests captured *before* the backup rather than against
      the backup's own inventory, so a self-consistent backup that lost evidence still
      fails.
      **Found by making it run, not by reading it.** Three bugs the design had not
      anticipated:
      - **Extensions are per-database.** An empty restore target has no pgvector, so
        the schema cannot be created — and because the restore deliberately tolerates
        `pg_restore`'s exit code (see P10.B2), that failure was *silent* until the
        post-restore check reported four evidence records missing for a reason that
        had nothing to do with the backup. This is the argument for verifying the
        restore rather than trusting the tool's mood.
      - **`pg_restore`'s diagnostics were being discarded**, because `allow_errors`
        swallows them and the post-restore check reports the *consequence*. "4 evidence
        records missing" with no indication of what `pg_restore` actually said is not
        debuggable. Its output is now attached to the failure.
      - **The test passed two different databases to one operation.** `pg_dump` was
        given the development DSN while the manifest was read from the isolated scratch
        database, so the archive and the manifest described different systems. That is
        the failure a backup tool must never have, and it was in the test.
      Each of the eight negative tests asserts the failure message names the specific
      evidence record — "the backup is incomplete" is not actionable at 3am.
- [x] T10.4 No job loss under load. **Job loss and double-claiming are asserted;
      latency is measured and reported, not asserted** — no SLO has been chosen, and
      inventing one here would make this file the first to disagree with reality.
      See P10.B5 for what the first run got wrong.
- [x] T10.5 Air-gapped: outbound blocked, local model still serves the agent.
      Fourteen tests. The negative ones matter most: an empty allowlist permits
      nothing public; a `.local` name is treated as egress; a scheme-pinned entry does
      not permit plaintext; a malformed URL is refused rather than crashing or being
      waved through; and `resolve_model` raises `EgressBlocked` for a cloud provider
      while a local one is unaffected. A cloud provider being *blocked* is asserted
      separately from "no model configured", because those are different problems with
      different fixes and the operator has to be told which they have.
- [x] T10.6 CPU analyzers available, GPU modules report unavailable, profile wired.
      Fourteen tests. Two directions matter and both are asserted:
      - A GPU module on a CPU host is unavailable **and says why** — which separates
        "this host cannot run this" from "you have not finished installing it".
      - The CPU set is **non-empty**, so a profile that silently stopped working is a
        test failure rather than a deployment nobody notices. Without this the
        hand-written capability list would pass today.
      - A GPU host reports the *same module list* with everything available, so
        `available` cannot be a constant that satisfies the CPU test.
      - No GPU device is passed to any container, and the GPU worker is excluded:
        setting `GPU_AVAILABLE=false` is a *claim*; the absence of a device mapping is
        the fact. A profile that claimed CPU-only while still passing `/dev/dri` would
        let a GPU module start and then fail at execution — exactly what the
        capability list exists to prevent.
      - The `/capabilities` endpoint is asserted against the computed list, field for
        field. It once re-serialised each field by hand and a field added to
        `Capability` silently did not appear — which is how `tier` went missing once
        already.
- [x] T10.7 Readiness flips before termination; new work refused; reads survive.
      Ten tests. The **ordering** assertions are the load-bearing ones, because every
      way a shutdown can go wrong presents the same symptom — requests failing — and
      what differs is whether the caller gets a clean retry or a half-written report.
      The signal handler is tested by invoking the installed handler rather than by
      sending a real SIGTERM, which would take the test runner down with it. before termination
**Exit**
- [~] P10.E **Not exited, and the gaps are named.** What Phase 10 delivers is real:
      backups that verify cryptographically and restore (T10.3), egress enforced with
      the surface AST-pinned (T10.5), a graceful drain in the correct order (T10.7),
      a constrained-host profile that can actually run the platform (T10.6), alert
      rules proven to fire (T10.2), a load check measuring no job loss (T10.4), traces that
      span the queue boundary with a real parent link and are stored with their case
      (P10.B1/T10.1), and a threat model brought back in line with the code.
      **Three things stand between this and a clean exit**, each recorded where it is
      built rather than only here:
      1. **A penetration test (P10.B5).** CI scanners that are themselves proven to
         fail prove the scanners work, not that the application has no findings.
      2. **PITR and MinIO-side replication (P10.B2).** `pg_dump` is a logical backup;
         point-in-time recovery needs WAL archiving.
      3. **Seven Phase 9 items (B1–B5, T9.2, T9.3)** blocked on labelled fixtures that
         cannot be manufactured, with the enabler named.
      **Grafana dashboards are not asserted** against a rendering Prometheus; only
      Prometheus and the rules are.

---

## Licensing
- [x] Apache 2.0 for the source, with the model weights explicitly excluded.
      **There was no `LICENSE` file at all** while this project was described as
      completely open source, which is the sort of gap that is invisible until
      someone tries to use or contribute to it.
      Chosen over MIT for the **patent grant** — a lab may well deploy this
      commercially, and Apache 2.0 grants patent rights explicitly where MIT grants
      none. Its attribution requirements also fit how this project already works: every
      weight is digest-pinned and its source recorded, which is the same discipline
      section 4(d) codifies.
      **`THIRD_PARTY_NOTICES.md` exists because a licence on the code does nothing for a
      file downloaded from someone else's server.** It names each pinned artifact, its
      upstream, its digest and its terms — which is checkable rather than aspirational,
      because the digest in `fetch_models.sh` is the digest you get.
      It also flags the question that is easy to miss: **training-data terms are
      separate from the weights' own licence.** SFace and YuNet are MIT in the OpenCV
      Zoo and were trained on VGGFace2 and WIDER FACE respectively; the weights' licence
      says nothing about the training data's.
      Note: the README build status was also stale ("Phase 5 complete; Phase 6
      begun") and is now current. A README that describes a smaller project than the
      one shipped is the same drift as a stale capability list — it is just read more
      often.

## Cross-cutting quality gates (enforced every phase)
- [x] X1 pytest unit+integration on live Postgres + MinIO (CI `integration` job)
- [x] X2 Playwright E2E against the real compose stack (CI `e2e` job). 11
      browser tests: the case journey, the ELA overlay asserted by `naturalWidth > 0`
      because a broken image is still "visible" to Playwright, the refusal path
      (a generated PNG run through ELA reports "not measured" and "No score was
      produced" rather than a zero), report generation and verification, and the
      accessibility suite below. No mocks anywhere: a mocked test confirms the
      component tree, and every defect this project found in the frontend was
      invisible to one. Auth is a setup project rather than a per-test sign-in,
      because `/auth/login` allows five attempts a minute and a suite that signs in
      per test spends the budget and fails on a 429 that looks nothing like the bug
      being hunted.
- [x] X3 Tool contracts, asserted against the **registry** rather than tool by
      tool. A per-tool suite is a list someone has to remember to extend, and the
      tool added without one is exactly the tool nobody checked.
      Four contracts, in descending order of damage: `state_changing` matches an
      explicit list (a writing tool with the flag unset applies changes with no human
      in the loop and nothing objects); every state-changing tool's description says
      a person must approve it; every argument is described and required-ness is
      honest; every tool's schema reaches the model intact.
      **Found three real violations on the first run:**
      - **No args model forbade unknown fields.** Pydantic ignores them by default, so
        a model that invents `confidence=0.9` gets a confident-looking result back and
        no indication its constraint was discarded. Fixed once, in a `ToolArgs` base
        class, so a tool added tomorrow cannot skip it.
      - **`write_memory` never said it needed human approval.** `update_case_finding`
        did. The model proposing a write had no way to know a person would be asked.
      - **Most argument fields had no description** — the model emits arguments from
        the schema, so an undescribed field is one it guesses at.
- [x] X4 Agent eval harness, over **scenarios as data**: each is a fixture case, an
      objective, and predicates over the finished run, so adding a behaviour means
      adding a row rather than writing a test. Four scenarios, covering exactly the
      properties the system's premise rests on — cite every claim, refuse rather than
      invent when there is nothing to draw on, a *proposed* write is parked rather
      than performed, and it stays inside its own case.
      **The harness is itself tested, against a deliberately wrong run.** An eval with
      no negative control is a script that has never been shown to fail, and an
      assertion that cannot fail is not an assertion. `tests/agent_fakes.py` composes
      a run that violates every rule at once — an uncited claim, a fabricated
      `result_id`, a write performed without approval — and the harness must reject it.
      A correct run must also pass, per scenario, or a harness that rejected everything
      would satisfy the control.
      **Building the control found a real error in a predicate.** The first
      `wrote_nothing` asserted *no write tool call*, which rejected the correct
      outcome for the scenario that exists to tell proposals from performances —
      conflating them is the exact confusion HITL exists to prevent. Now judged on the
      *result*: a parked action has a call and no result.
      **Measured against the live model, honestly: 1 of 4 scenarios passed, and the
      other three did not fail their predicates — the model timed out.** `qwen2.5:3b`
      on this hardware cannot return a tool call within the 60 s budget when seven
      tools are advertised. That is a real measurement of this deployment, recorded
      rather than hidden; it is also why the live scenarios are opt-in and reported
      rather than a CI gate. The hermetic controls are the gate; the live run is a
      report to read, and a regression in that list is the signal.
- [x] X5 Golden tests for every analyzer, on generated fixtures, no simulation.
      Exact floats are pinned, not `approx`: the analyzer's `model_version` already
      records the library versions behind a score, so a bump *should* be a reviewed
      act, and these tests are what make it visible. A golden test loosened to
      approximate would record nothing worth recording.
      **Found dead logic.** Every EXIF fixture this project had tripped only
      `no_exif` — each one either carried no metadata or was stripped down from
      something that had none. So `no_datetime_original`, `no_camera_identification`
      and `datetime_inconsistent` were executed by *nothing*: three of the module's
      four indicators, and the only ones that actually describe provenance. Added
      `exif_with(**tags)` to build a case carrying exactly the tags given, and a
      golden assertion per indicator. A module can pass every test it has while half
      its logic never runs.
      The object-detect golden uses the committed `bus.jpg` with its digest pinned,
      because a synthetic gradient exercises the decode path rather than the model —
      and it asserts the *class names*, since a detector reporting different objects
      would still pass a count. A second test asserts the detector **cannot** find a
      firearm, which is the documented limitation; if a future model swap changed
      that, the documentation is forced to be revisited rather than going stale.
      The face-compare golden asserts the direction, not the cosine: two different
      people must not compare above threshold, on any threshold this deployment
      might legitimately set.
- [x] X6 CI security: Bandit, Semgrep, pip-audit, Trivy, gitleaks. The advisory
      `|| true` on pip-audit is gone — the CVE backlog it was covering is cleared,
      and `|| true` on a security gate reads as passing in the UI while checking
      nothing.
      **And the gates are now proven to fail.** A scanner that has never been seen to
      reject anything is indistinguishable from one that is checking nothing: a
      broken rule path, an empty include glob, a mistyped binary — all exit 0 and
      look exactly like a clean scan. `scripts/verify_security_gates.py` plants one
      of each problem in a temp directory (an AWS-shaped key for gitleaks, MD5-for-
      password and a hardcoded password for bandit, `shell=True` for Semgrep, and a
      requirements file pinned to a release with a published advisory for pip-audit)
      and asserts the scanner rejects it. A gate that cannot be made to fail is
      **reported as a failure, not skipped** — "the scanner said nothing" is the
      outcome this exists to detect.
      Verified locally: bandit and pip-audit both reject their planted problems.
      gitleaks and Semgrep are not installed on this host, and the script says so
      rather than claiming four of four — an earlier version of it printed "every
      gate rejected the problem planted for it" with all four skipped, which is the
      exact false assurance the script exists to prevent, committed inside the
      script. CI installs and checks all four.
- [x] X7 Ruff lint + format clean; mypy strict on `app`
- [x] X8 Compose core profile boots, migrates, and serves in CI

---

## Future improvements (see FUTURE_IMPROVEMENTS.md)
- [x] FI-1 Case timeline auto-assembly. **The timeline shipped permanently empty.**
      Its empty state read *"Events appear as evidence is uploaded, analyses run and
      findings are recorded"* and nothing did any of those things — `POST
      /cases/{id}/timeline` existed and was reachable from no UI at all, so a case's
      chronology was visible only by calling the API by hand. That is the same class
      of defect as the stale README build status: a promise made in the interface that
      the system does not keep.
      Derived **on read, never written to `timeline_events`.** That table is the
      examiner's own record; a derived row written there would be indistinguishable
      from their entry, would survive this code being corrected, and could not be
      retracted. Sources: evidence received, evidence accessed, analyses completed,
      findings recorded.
      **Derived timestamps are when the platform recorded a step, not when the event
      happened**, and every entry says which it is — "Evidence received", never
      "photograph taken". A timeline that blurs the two lets an examiner read a
      processing time as an event time, which is the specific confusion this exists to
      prevent. Every entry carries `derived`, so an examiner writing "Evidence
      received: photo.jpg" cannot pass as the platform by choosing their words.
      Opt-in via `?derived=true`, so existing consumers see exactly what they saw.
      **EXIF capture times deliberately excluded** — `image-exif` records which fields
      were *present*, not their values, so re-reading the object store here would turn
      a list endpoint into an unbounded scan.
      **Four defects found by building it, none by reading it:**
      1. **The tab crashed on every undated event.** `b.occurred_at.localeCompare(...)`
         on a field the API sends as `null`. `tsc` was clean because
         `TimelineEventRead` was hand-written declaring `occurred_at: string` — plus
         `kind` and `detail`, which the API never sent and which rendered as an empty
         badge and no body text. The type is now the wire, and ordering moved into
         `orderTimeline` where `node --test` can reach it.
      2. **`uncertain` was invisible.** A first-class backend field, documented as
         "must be visibly uncertain in the export and UI", rendered nowhere. It now
         shows, as does `source_ref`.
      3. **Recorded entries came back with synthetic ids.** `merge` rebuilt every
         entry through the derived-id helper, so the id a client would use to fetch or
         cite a *recorded* event resolved to a row that does not exist. Only entries
         with genuinely no row get a synthetic id now.
      4. **One upload appeared twice.** The arrival was reported from both the
         `Evidence` row and the `upload` custody row, with identical wording — which
         reads as two separate occurrences. The custody allowlist is now `("access",)`.
      19 tests (11 backend through the HTTP API, 8 frontend). The backend ones assert
      the negative properties that make this safe: nothing is persisted, a derived
      entry is never marked `uncertain` (that would dilute the flag), ids are stable
      across requests (React keys by them, so a regenerated id remounts the list and
      drops focus), and an undated entry stays visible rather than being dropped.
- [x] FI-2 Duplicate / near-duplicate evidence detection. **Exact duplicates are
      already handled at ingest** — `IngestService.ingest` looks up the content hash
      within the case, records a `upload-dedupe` custody row and an audit entry, and
      returns the existing evidence. So the half of this that is "detect a duplicate"
      ships; what is missing is *near*-duplicate detection.
      **Measured before building, and the obvious approach does not work on the
      fixtures this repository actually has.** Difference hashing (dHash, 64-bit,
      pure numpy, no weights) over `tests/assets/*.jpg`: the only same-person pair,
      `turing_a`/`turing_b`, scores a Hamming distance of **31** — *worse* than 15 of
      the 27 different-person pairs, which range 25–38. Not separated.
      **The reason is a category error, and it is worth writing down.** `turing_a` and
      `turing_b` are two photographs of one person; they are not near-duplicate
      *images*. dHash measures perceptual similarity of the picture, which is the
      right target and the wrong pairing — and note that `image-prnu` already answers
      the "same camera" question honestly, which is a different and better-posed
      question. Detecting near-duplicates needs genuinely near-duplicate pairs: the
      same image resized, re-encoded, cropped or re-compressed. This repository has
      none, and manufacturing them from its own fixtures would mean asserting ground
      truth I created, which is weaker evidence than it sounds.
      **Not built yet.** The blocker is fixture design, not technique, and it is a
      smaller blocker than the Phase 9 licence wall — generated pairs are legitimate
      here because a deterministic algorithm's correctness does not depend on the
      pairs being real photographs.
      **Built as `near-duplicate`, Tier B, pure algorithm, no weights.** 64-bit
      difference hashing (dHash) over Pillow-decoded pixels. The generated-pair
      measurement, which is what settled it:

      | | dHash distance |
      |---|---|
      | re-encode q60 / resize 50% / resize 75% | 0–1 |
      | brightness +8% | 0–3 |
      | centre-crop 5% | 5–14 |
      | **unrelated images** (28 pairs) | **25–38** |

      Threshold 15, sitting in that gap. **Variants are derived in the test, not
      committed** — re-encoding each of the eight fixtures would be a second copy of
      every asset to keep in sync for no gain, and Pillow is pinned in `pyproject.toml`
      so the derivation is deterministic and the pinned distances hold.
      **The crop ceiling is the part worth having, and it is asserted.** Per crop
      fraction, worst same-image fixture: 5% → 14 bits, **10% → 24, 15% → 30, 20%
      → 41**, against unrelated images whose *minimum* is 25. The ranges stop
      separating between 5% and 10%, so **this module cannot separate a duplicate
      from an unrelated image beyond roughly a 10% crop.** A test asserts that
      touching (`max(cropped) >= min(cross) - 1`) so a future change that quietly
      widens the crops has to update the stated tolerance rather than let the limit
      become wrong silently. The payload's "not a match" branch therefore says *not
      shown to match* and never *shown not to match*.
      **What a match does not establish**, stated in the disclaimer and the
      limitations: that two files are the same photograph. A screenshot of a
      screenshot, two renders of one chart, or a collage from one source all match.
      Nor is it a manipulation signal, nor a statement about who, where or when.
      **Distinct from `image-prnu` and the payload says so**: that one asks whether
      two frames came from the same sensor (a provenance question), this asks whether
      two images look alike (a triage question). Neither substitutes for the other.
      Reference is a second evidence item via `reference_evidence_id` — the same
      case-scoped, hash-verified path `face-compare`, `image-prnu` and
      `text-authorship` use. An inline `params.reference_text` is ignored rather than
      used, so a comparison can never rest on an unrecorded document.
      22 tests, including exact per-transform maxima so a drift in Pillow or the
      transform is visible rather than silently changing what this reports.
      **The earlier negative result was mine, and it was a fixture error, not a
      method error** — recorded above because a wrong measurement left in place would
      have blocked this item indefinitely on a false premise.
- [x] FI-3 Redaction tool for exports. **The deliverable is the verification, not the
      removal.** A redaction an operator believes in and that did not take effect is
      worse than no redaction: it converts an exposure into a concealed one. So
      `collect` runs *before* `apply`, and `verify_absent` is handed the actual values
      that were taken out and checks the serialised bytes for them.
      **This is the distinction the whole module turns on.** A token sweep — scanning
      the output for something shaped like an officer id — passes just as happily on an
      export where the redaction silently did nothing, because the placeholder is
      present either way. Checking the real UUIDs cannot. A test asserts exactly that:
      a document containing `"[redacted: officer] <uuid>"` is refused.
      **`verify_zip` reads inside the archive, not the container.** ZIP compresses, so
      a naive scan of the outer bytes finds no plaintext and reports success on an
      archive whose `case.json` still names the officer. A test proves the outer bytes
      contain no plaintext for that archive and that the member scan still catches it.
      **No pattern matching, deliberately.** A regex matching an email also matches a
      sha256 quoted in a finding, and "42 items redacted" without being able to say
      which 42 asserts something unsupportable. Redaction is by **named field** (an
      allowlist — a deny-list would grow silently as columns are added) and by
      **literal string** the caller names. Free text is passed through untouched
      unless named, and a test asserts that, so the limitation cannot drift into being
      over-claimed.
      **An unknown field name is a 422, not a silent pass-through** — asking to redact
      `owner` and receiving a clean export with nothing removed is precisely the
      outcome this prevents.
      `?redact=owner_id,assigned_to&redact_text=...` on `GET /cases/{id}/export`,
      covering JSON, CSV and ZIP. Applied in `exports.py` rather than the router so a
      format added later cannot accidentally ship unredacted. Each builder verifies its
      own output.
      **A redacted export is not evidence and is never signed.** It has had content
      taken out, so its digest no longer matches the case file. The PAdES-signed report
      (P8.B1) is untouched by any of this. The response carries `X-Redacted-Fields` and
      an `X-Redacted-Notice`, so a document separated from the request still says it is
      incomplete, and the audit log records `case.export.redacted` as a distinct action
      from `case.export`.
      **The case id is deliberately kept** in the ZIP manifest: a manifest without it
      cannot be matched back to anything, and it identifies a case, not a person.
      Asserted, so a future "redact every id" change has to confront that trade-off
      rather than make it silently.
      A failure inside the exporter returns **500, not 4xx** — reaching it means the
      codebase produced a document still holding a value the caller asked to have
      removed, and the client did nothing wrong by asking for redaction.
      **One real bug found by a test**: `collect` took `str()` of a redacted field's
      value, so a field holding a *list* produced the list's repr — which matches
      nothing in the output, and the absence check then passed on an export where the
      values were still present. Exactly the silent pass this module exists to prevent,
      now flattened and covered.
      21 tests.
- [x] FI-4 Multi-case entity linking. `GET /cases/{id}/entities/links` — entities on this
      case that also appear on another case the caller can already see. A person,
      vehicle, device or address recurring across two investigations is one of the more
      valuable things a case system can surface, and it is invisible while entities are
      scoped to a single case.
      **A link is a candidate, never an identity.** Two cases both containing
      `person: "Jane Doe"` is evidence that two people with the same name were
      recorded, not that they are the same person — likewise a vendor-recycled serial
      and a re-let address. `is_candidate_only` is in every link and the disclaimer
      says what it cannot establish.
      **Matching is exact on a case-folded, whitespace-collapsed value, and nothing
      fuzzy.** Case-folding and whitespace are not judgement calls; the Unicode NFC
      normalisation matters specifically for entity resolution, because the same name
      from two machines arrives in different normal forms and byte equality would report
      one person as two. Beyond that: nothing. No edit distance, no initials, no
      phonetics. **A confidence score on a guess reads as a judgement**, and in a
      forensic tool that is the failure mode rather than a feature — so
      `"Jane Doe"` vs `"Jane Do"` returns no link at all, which is the honest answer
      and is asserted.
      **Cross-case reads are the whole risk, handled in SQL.** `case_scope_filter` goes
      onto the `Case` join, *not* onto the results: filtering in Python would still
      load an unauthorised case off disk and only scrub the response, and a co-occurrence
      is reported **only when the caller can see both cases**. Reporting "this name also
      appears somewhere you cannot see" would confirm both the existence and the content
      of a case the caller was refused — the exact thing the 404-on-out-of-scope rule
      exists to prevent, so an invisible occurrence is omitted entirely rather than
      hinted at. A test asserts a link to another officer's case is absent, *not*
      redacted, and a reviewer/admin sees the same data the officer cannot — same query,
      different authorisation.
      A positive control asserts the scope filter is **not over-tight** for the cases a
      caller owns. **That test was wrong the first time and the bug is worth recording**:
      its `_user()` helper generated a random user id, so "both cases owned by one
      officer" was in fact owned by neither and asserted a link that scoping correctly
      suppressed. It failed rather than passing — which is the only reason it was caught
      at all, and a reminder that a green fixture is not the same as a correct one.
      Duplicate values within one case collapse to one link; two rows are not two
      findings. Searching for links is audited as `entity.links.read` with the *count*
      rather than the values — the act of asking who searched for cross-case links is
      itself a record worth keeping. The explanation of what matching does and does not
      attempt is served from the backend, not duplicated into a client.
      14 tests.
      **Also fixed here:** `EntityRead` in `frontend/src/lib/types.ts` declared
      `label`, which the API has never sent — the field is `value`. Sixth
      hand-written duplicate of a backend schema in this session, and the same defect
      as `TimelineEventRead`: written from an assumption, and `tsc` unable to notice
      because it trusted the duplicate. Nothing renders entities yet, so nothing had
      broken, which is the only reason it is worth fixing now rather than when the
      first entity UI reads `undefined`.
- [x] FI-5 Agent second-opinion reviewer mode. `GET /agent/cases/{id}/second-opinion
      ?first=&second=` — two runs over the same objective, side by side, with their
      disagreements named.
      **A second opinion from the same model is not corroboration, and the design is
      built so it cannot be misread.** `resolve_model` picks one provider; a second run
      through it is a second *sample* from the same distribution, not a second witness.
      If both runs agree that is evidence about the model's **consistency and nothing
      about whether either conclusion is right** — a confidently wrong model is
      consistently wrong, and agreement is exactly what that looks like. Two agreeing
      reports presented as corroboration would be the most misleading thing this system
      could do with its own output, so `is_corroboration: false` is set
      **unconditionally** — including when the runs happen to agree, which is the case
      most likely to be over-read.
      **The output is divergence, not a verdict.** What is useful about running an
      investigation twice is where the two *disagree*: a claim present in one and
      absent from the other is either a gap in the first or noise in the second, and
      either way it is where a human should look. **No run is ranked and no winner is
      declared** — picking for the examiner removes the judgement that made them the
      examiner.
      **Claims are matched on the set of result ids behind them, not their prose.** Two
      runs reaching one conclusion word it differently, and matching on text would
      report every paraphrase as a disagreement and bury the real ones. Three divergence
      kinds: `only_in_first` / `only_in_second` (the signal), `different_basis` (both
      said it, resting on different evidence — reads as agreement in a summary and is
      not), and `unsubstantiated` (a claim with **no citations at all**, which
      `ProvenanceGuard` should make impossible; it appearing means the guard was
      bypassed, so it surfaces rather than being treated as a link whose signature
      happens to be empty).
      Both runs are read through `_scoped_run`, so a run from another case or another
      officer's case is a plain 404 and discloses nothing; a run readable on its own but
      belonging to a different case than the path names is also 404, because comparing
      across two investigations at once is not what was asked. Claims serialise through
      the same shape `AgentState.to_dict` builds, so what is compared is what is stored
      in the checkpoint rather than a second serialisation that could drift. Reads are
      audited as `agent.second_opinion.read`.
      **One test bug worth recording**: `test_no_winner_is_ever_declared` swept the whole
      payload for the words "winner", "preferred" and "rank" — and failed on the
      disclaimer text *"this module names no winner and ranks nothing"*, i.e. on the
      very sentence making the guarantee. Narrowed to scan field *names* with the prose
      fields excluded. A substring assertion over prose is how a test ends up demanding
      the removal of the warning it was written to enforce.
      11 tests.

## Open defect found while verifying FI-1 (not caused by it)

`e2e/journey.spec.ts` → *"an action parked for approval stays answerable after a
reload"* **times out at its 180 s budget when the full suite runs, and passes in
isolation in ~15 s.** Recorded rather than left as an unexplained flake.

The mechanism is measured, not guessed. The failure is
`locator.click: Test timeout of 180000ms exceeded` waiting for the Findings tab after
the approval is answered — and answering it lets the agent continue, which costs a
real `qwen2.5:3b` call through Ollama. The test's own comment already documents this
class of problem ("the budget is the model's, not the browser's"); what it has not yet
done is stop depending on the model's speed.

**Attribution, stated precisely.** Playwright runs files alphabetically with one
worker, so `a11y`, `journey` and then `timeline` — the three new timeline tests run
*after* the failing one and cannot cause it. Confirmed by running `journey` + `a11y`
alone: 12 passed. Restarting the API first (to rule out the in-memory auth rate
limiter accumulating across runs) did not clear it. The derived timeline endpoint is
not the cost either: 13 ms against 8 ms for the plain one.

**What would actually fix it:** the test asserts browser behaviour — that a parked
action is answerable after a reload, which is the bug it was written for. That needs
no live model at all. The approval endpoint already parks an action without one, so
the continuation after approval is the only part that needs the model, and it could be
stubbed the way `agent_fakes.py` stubs the rest. Left undone deliberately rather than
patched with a longer timeout, because raising the budget hides the dependency instead
of removing it.
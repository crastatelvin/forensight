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

### End-to-end tests

```bash
docker compose -f docker-compose.yml -f docker-compose.e2e.yml --profile core up -d --wait
cd frontend && npm run build          # the config's server timeout is too short for
                                      # `npm run build && npm run start` combined
npx next start &                      # or: npm run start
PLAYWRIGHT_WEB_PORT=3000 npx playwright test
```

**The second compose file is required**, and running the suite without it is a
trap rather than merely slower. It makes three changes, each for a reason that
will not be obvious from the symptom:

- **A stub model** (`llm-stub`). The agent-journey test asserts that a parked
  action stays answerable after a reload and that approving it performs the
  write; neither depends on what the agent decides next. Approving does let the
  agent continue, and on this CPU-only host `qwen2.5:3b` generates at about
  **7 tokens/second** — 256 tokens is 38 s — so the continuation varied between
  8 s and 69 s and the suite failed intermittently. The stub answers with an
  immediate empty completion; the application still exercises its real HTTP
  client, tool-calling normalisation, tool runner and persistence.
  **Not** a `LLM_PROVIDER` switch in the product: a setting that swaps the
  agent's reasoning for a canned reply is what gets left enabled in production.
- **Higher rate-limit budgets.** The limiter is keyed by IP, so every request in
  the suite shares one bucket per category. At the production mutation budget of
  60/minute the suite ran out mid-run and failed on a **429 from an upload, at a
  random point**. The defaults in `docker-compose.yml` are unchanged — those are
  what a deployment runs, and the limiter is asserted directly by the backend
  tests.
Nothing in that file changes application behaviour. The journey spec's own
`test.setTimeout(180_000)` lives in `frontend/e2e/journey.spec.ts`, where it
belongs: the run it guards is the test's, not the stack's.

### The container is unhealthy after a schema change

`alembic upgrade head` aborts if a table already exists, and then the API is
unhealthy with nothing in the health output pointing at migrations — it looks
like an application that cannot start. Check the logs for `DuplicateTable`, then
compare the schema against the version table:

```bash
docker compose logs api | grep -i duplicate
docker compose exec -T postgres psql -U forensight -d forensight \
  -c 'select version_num from alembic_version;'
```

If the table is already correct, stamp rather than re-run:

```bash
docker compose run --rm --no-deps -T --entrypoint sh api \
  -c 'uv run --no-sync alembic stamp 0008_traces'
```

**Check the shape before stamping.** `alembic stamp` only moves the version
pointer; it creates nothing. A table that exists with the *wrong* columns will
then pass the boot check and fail at query time, which is a worse failure than
the one you are fixing.

### `ELA cannot be applied: the evidence did not decode`

Correct behaviour, not a fault. The ELA endpoint needs real image bytes, and it refuses
with a reason rather than rendering something meaningless.

**If the row is named `seed.jpg` and is one byte**, it came from
`backend/scripts/seed_parked_run.py`, which deliberately inserts a placeholder so the
agent-approval journey has a case with evidence in it. Nothing verifies that file's
bytes. Use a real upload to exercise ELA:

```bash
curl -X POST -H 'Content-Type: image/jpeg' --data-binary @real.jpg \
  'http://localhost:8000/api/cases/<case-id>/evidence'
```

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

### Reading a trace

Every API response carries `x-trace-id` and `x-trace-span-id`. A submission's trace
follows the job into the worker, so the API span and the worker's span are **one
trace**, not two:

```bash
curl -s "localhost:8000/traces/$TRACE_ID?case_id=$CASE" \
  -H "Authorization: Bearer $TOKEN"
```

`case_id` is **required**. A trace readable by id alone would let any signed-in user
time other people's investigations by guessing trace ids, so a trace is only readable
in the context of a case you may open, and every span in it must belong to that case.

Spans are **stored in Postgres, not exported to a collector** — no OpenTelemetry SDK,
no image growth, and the egress allowlist stays meaningful on an air-gapped host. The
trade is that there is no sampling, aggregation or flame-graph UI: this answers "what
happened to this job, in what order, and how long did each part take", which is the
question a stuck analysis raises. Nothing in the read path can slow a request down —
span writes are best-effort and a failure to record is never a failure to serve.

If you are adding instrumentation, note that `tracing.finish()` coerces attributes to
something `jsonb` accepts. Do not skip it: an uncoerced `uuid.UUID` raises at flush
time, and because span writes swallow their own errors, the result is a trace that
appears to work and records nothing.

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
- `WEB_TIMEOUT` sets the gunicorn worker timeout (default 900s). **It must exceed
  the longest agent run.** `GET /agent/runs/{id}/stream` holds a worker open for
  as long as the model works, and gunicorn SIGKILLs a worker that exceeds this —
  while logging *"Perhaps out of memory?"*, which points at the wrong thing
  entirely (the container's cgroup records zero OOM kills). A stream that stops
  after roughly 30s with no `done` event is this, not a memory problem. Lowering
  the timeout trades a tight reap for streams that get killed; a deployment that
  wants both should serve SSE from a separate process.
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

### Report signing

Case reports are PAdES-signed, and **there is no unsigned mode**. With no key
configured, `GET /reports/cases/{id}.pdf` answers 503 naming the provisioning
command rather than emitting a PDF that carries no integrity claim — an unsigned
report looks byte-for-byte like a signed one until someone relies on the
difference.

```bash
# Runs inside the container so the key is written straight into the volume and
# never lands on a host filesystem or in an image layer.
#
# `uv run --no-sync python`, **not** `python`: dependencies live in /app/.venv and are
# not on PATH, so the bare `python` is the system interpreter and dies with
# `ModuleNotFoundError: No module named 'cryptography'`. This was the documented command
# in this runbook, in the README and in CI, and it had never worked.
docker compose exec api uv run --no-sync python scripts/provision_signer.py
```

It prompts for a passphrase and writes a PKCS#12 at mode 0600 into the
`signingkeys` volume. Set `REPORT_SIGNING_PASSPHRASE` in `.env` — the passphrase
belongs in the environment, not beside the key and not in version control.

**Distribute the certificate, not the key.** To read the certificate back out for
anyone who needs to verify reports:

```bash
docker compose exec api python - <<'PY'
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
raw = open("/var/lib/forensight/signing/report-signer.p12", "rb").read()
_pw = input("passphrase: ").encode()
_k, cert, _ = pkcs12.load_key_and_certificates(raw, _pw)
print(cert.public_bytes(Encoding.PEM).decode())
PY
```

Verification (`POST /reports/verify`, or the **Check a report** panel) answers two
questions separately, and the distinction is load-bearing:

| Result | Means |
|---|---|
| `intact: true`, `trusted: true` | Bytes unchanged since signing, and the signer chains to a certificate you supplied. |
| `intact: true`, `trusted: false` | Bytes unchanged. Signer **unconfirmed** — self-signed is normal here. Not a failure. |
| `intact: false` | The document does not match its signature. It has been altered. |

A signing key that is replaced does not invalidate reports signed with the old one
for integrity purposes — the bytes are still verifiably unaltered — but readers will
no longer see it as *trusted* unless they hold the old certificate. Keep the old
certificate; that is what makes historical reports verifiable.

### Long-term signature validation (PAdES baseline-LT)

**What it buys, and why it is not optional in the long run.** A baseline-B signature
proves the bytes have not changed and nothing about *when* they were signed — the
signing time is the machine's own claim. Worse, **it stops working when the signing
certificate expires.** A report produced by a lab whose certificate later lapses
has no fallback.

Baseline-LT fixes both: a document timestamp counter-signs the signature, and the
signing chain plus any revocation material travel inside the PDF, so verification
still works years later.

**A timestamp authority, hosted.** Any RFC 3161 authority works. For an air-gapped
lab, this project ships one — the cryptography is OpenSSL's, not ours:

```bash
# One-time, into the signing volume so it survives a container recreate.
docker compose exec -u root api sh -c \
  'mkdir -p /var/lib/forensight/signing/tsa && chown appuser:appuser /var/lib/forensight/signing/tsa'
docker compose exec api uv run --no-sync python scripts/provision_tsa.py \
  --out-dir /var/lib/forensight/signing/tsa
# self-test passes only when the authority issues a timestamp for its own request

# Then run it as a service. **Not** `exec -d`: a process started that way does not
# survive the api container being recreated, and REPORT_TIMESTAMP_URL then points at a
# socket that is not there — which fails every report signature rather than degrading to
# baseline-B.
docker compose --profile tsa up -d --wait tsa
```

Then in `.env`:

```
REPORT_TIMESTAMP_URL=http://<host>:8080/timestamp
REPORT_TIMESTAMP_CA=/var/lib/forensight/tsa/tsa.crt
```

**`REPORT_TIMESTAMP_CA` is required, not optional.** Signing validates the
countersignature's chain in order to embed it, and a self-signed authority has no
path to anything else unless it is named as its own anchor. Without it every
signature fails with `The X.509 certificate provided is self-signed`.

**A TSA is only worth its certificate.** One this lab hosts proves the time against
the lab's own key — the right answer air-gapped, and the wrong answer if the verifier
trusts nobody's key. Which is why verification reports `timestamped` and `trusted`
separately:

| Result | Means |
|---|---|
| `timestamped: true`, `trusted: false` | A countersignature is present and the document is intact. Its authority is **not one you trust**, so it establishes nothing to you. |
| `timestamped: true`, `trusted: true` | A countersignature from an authority you trust. The signing time is established. |
| `timestamped: false` | No timestamp. `level` is `BASELINE-B` and the signature will stop validating when the certificate expires. |

`level` is **measured from the bytes**, never copied from configuration: a report
signed before a TSA was configured, or produced by another tool, is described by
what it carries.

**Reading the level.** `BASELINE-B` is a signature alone. `BASELINE-T` adds a
countersignature. `BASELINE-LT` adds validation material — recognised structurally
by the Document Security Store, which is a weaker check than a full PAdES
conformance check and is reported as such.

**Two limits worth stating to anyone relying on it.**

1. **Appending bytes to a baseline-LT report is not detected.** Measured:
   `total_revisions` is unchanged by an appended PDF comment, and those bytes sit
   outside every signed `ByteRange` exactly as the Document Security Store does, so
   the append is indistinguishable from the archive update that legitimately
   follows. **The integrity of what was signed is unaffected** and every change that
   alters a page — content, a form field, an annotation — is refused. What is missed
   is the *fact* of the append. An appended comment renders nothing.
2. **`_measured_level` is structural, not a conformance check.** It reports
   `BASELINE-LT` when a Document Security Store is present, not because every
   requirement of ETSI EN 319 142-1 has been verified.

**Fetching revocation data is off.** Signing sets `allow_fetching=False`, so it
cannot reach an OCSP responder or a CRL distribution point at report time — that
would stall a request on a case and send a case identifier to a third party. Supply
CRLs on disk with `REPORT_REVOCATION_FILES` if your CA publishes any.

### Redacted exports

`GET /cases/{id}/export?fmt=json|csv|zip` accepts `redact=` and `redact_text=`:

```bash
curl -s "localhost:8000/cases/$CASE/export?fmt=json&redact=owner_id,assigned_to" \
  -H "Authorization: Bearer $TOKEN" -o redacted.json
```

`redact` takes a comma-separated list of field names from `owner_id`, `assigned_to`,
`uploaded_by`, `created_by`, `actor_id`. An unrecognised name is a **422** rather than
a silent pass-through — asking to redact `owner` and getting a clean export with
nothing removed is the failure this guards against.

`redact_text` takes newline-separated literal strings to scrub from free text. This is
the only way to remove something from a case description, a finding body or a
filename, and naming the exact string is deliberate: there is **no pattern matching**,
because a pattern that matches an email also matches a sha256 quoted in a finding, and
"42 items redacted" without saying which 42 asserts something unsupportable.

Every export is verified against the values it actually removed before being returned,
and a redaction that did not take effect is refused rather than sent. For ZIP this
means each member is read and checked — scanning the archive's outer bytes would find
no compressed plaintext and report success on an export that still names the officer.

Redacted responses carry `X-Redacted-Fields` and an `X-Redacted-Notice`, and the audit
log records `case.export.redacted` rather than `case.export`.

**A redacted export is not evidence and is never signed.** It has had content removed,
so its digest no longer matches the case file. The PAdES-signed report above is
unaffected; the case file remains the record.

The case id is deliberately kept in the ZIP manifest — a manifest without it cannot be
matched back to anything, and it identifies a case rather than a person.

### Comparing two agent runs

```bash
curl -s "localhost:8000/agent/cases/$CASE/second-opinion?first=$RUN_A&second=$RUN_B" \
  -H "Authorization: Bearer $TOKEN"
```

Returns both runs and a `comparison` naming where they diverge.

**This is not corroboration, and the response says so unconditionally.** Both runs go
through the same model and the same provider, so agreement between them is evidence
about the model's *consistency* and nothing about whether either conclusion is right —
a confidently wrong model is consistently wrong. Independent corroboration needs an
independent source: a different model, a different provider, or a human who worked the
case independently.

What is useful is the **divergence**. A claim present in one run and absent from the
other is either a gap in the first or noise in the second, and either way it is where
a human should look. Divergence kinds:

| kind | means |
|---|---|
| `only_in_first` / `only_in_second` | one run reached a conclusion the other did not — **the signal** |
| `different_basis` | both said it, resting on different analyzer results; reads as agreement in a summary and is not |
| `unsubstantiated` | a claim with no citations at all; `ProvenanceGuard` should make this impossible, so seeing it means the guard was bypassed |

Claims are matched on **the set of result ids behind them**, not their prose — two runs
reaching one conclusion word it differently, and matching on text would report every
paraphrase as a disagreement and bury the real ones.

No run is ranked and no winner is declared. Both runs must belong to the case named in
the path; anything else is a 404.

### Seeding a parked approval

To exercise the HITL gate by hand — or to reproduce what the browser test arranges —
park a state-changing action on a fresh case:

```bash
docker compose exec api python -m scripts.seed_parked_run \
  --email you@example.org --title "Approval check"
```

It prints the `case_id`, `run_id` and `action_id`. Open the case, go to the Agent
tab, and the gate is waiting: it shows the exact arguments the agent wants to use
before anything happens.

**What this script is, and is not.** It drives the *real* `AgentGraph` with a
one-shot model, so the action is parked by the same `NeedsApproval` branch a real
model hits. It exists because getting a model to *decide* to propose a write is
inconsistent on a 3B model, and a check that depends on that is flaky for reasons
that have nothing to do with what it is checking. It never lives in `app/` — it is a
fixture generator, not a way to make an agent misbehave in production.

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

Eleven modules ship today. `byte-entropy` is the Phase 4 reference module; the image
modules are the real forensic techniques. `/capabilities` is authoritative — it is
generated from the registry, so it cannot drift from what the build actually ships.

| Module | Tier | Score means | Needs |
|---|---|---|---|
| `byte-entropy` | B | Shannon entropy in bits/byte | nothing |
| `image-ela` | A | fraction of pixels with anomalously high error level | JPEG input |
| `image-exif` | B | fraction of metadata indicators that fired | JPEG input |
| `image-prnu` | B | correlation against a reference sensor fingerprint | 2 images from one camera |
| `geo-correlate` | B | distance from the evidence's own GPS to a supplied place | image with EXIF GPS + `compare_to` |
| `object-detect` | A | highest confidence among detections of the watchlist classes | pinned weights |
| `face-compare` | B | cosine similarity between two faces' embeddings | pinned weights + 2 images |
| `text-authorship` | B | cosine similarity between two documents' stylometric profiles | 2 text documents, ≥3000 words each |
| `near-duplicate` | B | bit distance between two images' 64-bit difference hashes | 2 images from one case |
| `speaker-compare` | B | cosine similarity between two recordings' ECAPA embeddings | 2 WAV recordings, ≥5 s, 16 kHz mono |
| `handwriting-match` | B | fraction of a page's ORB features matched to another capture | 2 page images |

`image-prnu`, `face-compare`, `text-authorship`, `near-duplicate`,
`speaker-compare` and `handwriting-match` compare two
items and take the second as `params.reference_evidence_id` — an evidence id in the same case, resolved
case-scoped and hash-verified before the comparison runs.

```bash
curl -X POST localhost:8000/cases/$CASE/analyses \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"module":"image-ela","evidence_id":"'$EVID'"}'
```

**`speaker-compare` has no calibrated operating point here.** Over the eight
public-domain LibriVox readings in `tests/assets/speaker/`, same-reader similarity runs
0.328–0.911 while different-reader similarity reaches 0.841 — the ranges overlap. A
match is a prompt to examine more audio; a non-match is **not** evidence that two
recordings are of different people. WAV only, 16 kHz mono, 16-bit, at least 5 s: it
refuses rather than resampling, because a silent resample would score 44.1 kHz against
16 kHz and produce a number that looked comparable and was not.

**`handwriting-match` is document matching, not authorship attribution.** It answers
"are these two images the same page", measured on four public-domain manuscript pages
of one 1914 diary (same page re-captured 0.380–0.749, different page 0.007–0.013). Two
pages sharing a hand says nothing about who wrote either — an author keeps one hand, a
form-filler imitates one, a forger copies one — and because every fixture is one writer,
that confound is **untested**. A non-match is not proof two documents differ: page
curvature, perspective, raking light, a different pen and a different scanner all defeat
this comparison.

**`text-authorship` does not currently separate authors.** On its six public-domain
fixtures, same-author similarity falls to 0.934 while a different-author pair reaches
0.957. The threshold is a screening value that flags a pair for a human to read, not
a determination — see `CHECKLIST.md` P9.B6 for the measurements and why the number was
not moved until the fixtures divided.

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

# Threat model — ForenSight

Scope: the platform as it now stands — auth, cases, the evidence vault, custody,
the audit chain, the agent and its tools, the analyzer registry, signed reports,
and the browser frontend.

This document was written when the platform was Phase 0–3 and said so at the top.
It was then left saying that the agent, the analyzers, the reports and the frontend
"need their own pass" — and every one of those shipped without it, so for a long
time the threat model described a system smaller than the one being deployed. That
is the specific failure this document exists to prevent, and it is the same failure
mode as the stale capability list: **a security document that drifts from the code is
worse than none, because it is read as current.** Phases 5–10 are now covered below;
the "open" rows are what is still genuinely open.

## Assets

| Asset | Why it matters | Where it lives |
|---|---|---|
| Evidence bytes | The product's entire output is only as defensible as these | MinIO `evidence` bucket, COMPLIANCE object-lock, key = SHA-256 |
| Custody + audit trail | Proves who touched what, and when | Postgres `chain_of_custody`, `audit_log` |
| Case metadata | Suspect PII, investigative detail | Postgres |
| Credentials | Full read/write on all cases | Argon2id hashes, hashed refresh tokens |
| Service secrets | DB/MinIO credentials, JWT signing key | Environment / SOPS |

## Trust boundaries

```
Browser ──HTTPS──▶ Reverse proxy ──▶ Next (BFF) ──▶ FastAPI ──▶ Postgres / MinIO / Redis
                                                     │
                                                     ├──▶ Agent ──▶ LLM provider
                                                     │      (egress-allowlisted)
                                                     │
                                                     └──▶ Queue ──▶ Workers ──▶ ONNX models
```

Three boundaries the earlier diagram did not have, each with its own section below:
the **Next server** holds the session cookies and is a trust boundary in its own
right; the **LLM provider** is the only egress this application makes; and the
**queue** is where trust context has to be carried by value, because process
identity does not survive it.

Everything crossing into FastAPI is untrusted: filenames, content-type headers,
upload bytes, `case.number`, JWTs, cookie presence, and `X-Forwarded-For`.

## Controls in place

**Authentication.** Argon2id (no plaintext, ever). Short-lived HS256 access
tokens; opaque refresh tokens stored as SHA-256 hashes, single-use, with reuse
detection that revokes the whole family. Login is rate-limited and answers
identically for unknown-user and wrong-password, with a dummy Argon2 verify to
keep the timing flat. `SECRET_KEY` is mandatory — the app refuses to boot
without it.

**Authorization.** Two layers, both enforced in code rather than per route:
RBAC by role, and case-scope object authorization. Officers and experts reach
only cases they own or are assigned to. A case the caller cannot see returns
`404`, not `403`, so an id cannot be probed for existence. Every request
revalidates that the account still exists and is active, and authorizes on the
*stored* role — so a demotion takes effect immediately rather than at token
expiry.

**Evidence ingest.** Filenames are sanitized and never used for storage paths.
MIME is sniffed from the file's own bytes, never from the client header. Size is
enforced while streaming, so the cap actually bounds memory. Uploads are scanned
before anything is persisted. Objects are content-addressed by SHA-256, written
to an object-locked bucket under COMPLIANCE retention, and re-verified against
their hash on every read — a mismatch refuses the read *and* records an audit
entry.

**Audit.** Append-only, hash-chained (`entry_hash = sha256(prev_hash ||
canonical(payload))`), verified by full-chain recomputation, exposed at
`GET /cases/audit/verify` so the claim is checkable rather than merely asserted.
Appends take a transaction-scoped advisory lock, because two concurrent writers
reading the same tail hash would otherwise fork the chain.

**Transport & browser.** Security headers on every response (CSP, nosniff,
frame-deny, no-referrer, permissions-policy), HSTS in production, CORS with an
explicit origin allowlist and credentials enabled, CSRF double-submit for
cookie-authenticated mutations, HttpOnly auth cookies with the CSRF cookie
deliberately readable.

**Known limits of the chain.** The chain is tamper-*evident*, not tamper-proof:
nothing at the database level forbids `UPDATE`/`DELETE`, and truncating the
*tail* of the log is undetectable (a prefix verifies fine). Detecting truncation
needs an external anchor — periodically publishing the head hash somewhere
append-only. Tracked, not yet built.

## Threats and mitigations

| Threat | Mitigation | Status |
|---|---|---|
| Path traversal via filename | `secure_filename`; storage key is the hash, never the name | done |
| Disguised file type | MIME sniffed from bytes; allow-list | done |
| Oversized upload DoS | Streamed size cap; 200 MiB | done |
| Malware upload | ClamAV before persistence; EICAR guard when no daemon | done (ClamAV optional) |
| Evidence tampering | Object lock + hash re-verified on read | done |
| Audit tampering | Hash chain + verifier endpoint | done (tail truncation open) |
| Audit chain forking | Advisory lock on append | done |
| IDOR across cases | Case-scope authorization on every route | done |
| Privilege via stale token | Per-request account + role revalidation | done |
| CSRF | Double-submit cookie; exempt only session-*establishing* endpoints | done |
| Brute force / credential stuffing | Per-IP rate limit, Redis-backed | done (per-IP only) |
| Rate-limit evasion via spoofed IP | `--forwarded-allow-ips` defaults to loopback | done |
| Header injection via `case.number` | Validated on write; rejected on create | done |
| Mass data extraction | Admin-only audit endpoints; scoped lists; pagination | done |
| SQL injection | Parameter-bound queries throughout | done |
| Secret leakage in repo | `.env` gitignored; gitleaks + Semgrep in CI | done |
| Known-vulnerable dependencies | Bandit + pip-audit + Trivy in CI | done (pip-audit advisory) |
| XSS in report/UI rendering | React escapes by default; the PDF builder escapes every interpolated value (`_esc`); no `dangerouslySetInnerHTML` anywhere | done (asserted) |
| Report produced without signing key | Endpoint 503s naming `provision_signer.py`. An unsigned report is byte-for-byte identical in shape to a signed one, so "it rendered" is not evidence it carries an integrity claim | done |
| Report tampered with after signing | PAdES baseline-B; `intact` and `trusted` are **separate** verdicts so a self-signed report cannot read as verified | done (measured: 82/82 single-byte flips caught) |
| Model artifact swapped | SHA-256 pinned per module and verified *before load*; the verified bytes are handed to the module so it cannot fetch its own on another route | done |
| GPU-only module on a CPU host | Reported `unsupported` by `/capabilities` **before** a job is submitted, not a 500 at execution | done |
| Analyzers reading each other's evidence | A module is handed bytes; it has no store handle, so it cannot reach another case. `geo-correlate` takes coordinates as *data* for the same reason | done (T9.6) |
| Queue job executed with no provenance | `job_id` + `requested_by` are columns with foreign keys; the result is written inside the same transaction as its terminal status | done |
| Shutdown truncating an in-flight report | Readiness drops **first**, new work is refused with a retry hint, in-flight work finishes, `/healthz` stays 200 | done |
| Agent prompt injection | Sanitise injection shapes, wrap evidence as labelled untrusted data, and enforce case scope in SQL so a jailbroken model is still bounded |
| Agent asserting an unsourced conclusion | Every citation resolved against a stored `analysis_results` row; unsourced, hallucinated, cross-case and paraphrased citations rejected. A model that cannot source a claim yields a recorded `refusal` |
| Agent silently changing case state | Every state-changing tool parks frozen arguments for explicit approval; approve and reject both audited; an approval cannot be replayed against different arguments |
| Agent reading another case's memory or evidence | Case id comes from the run, never from model arguments; memory scope is a SQL predicate (case + actor) |
| Agent tool calls going unaudited | Every call, including rejected ones, recorded with arg hash and result hash |
| Labelled data exfiltration by a user | `EGRESS_ALLOWED_HOSTS` gates the only outbound call in `app/`; **empty means no public host**, so air-gapped is the default rather than a mode someone must switch on | done |
| Egress control silently losing coverage | An AST scan of `app/` fails the suite if a second outbound HTTP client appears. Without it the control would keep *reporting* the deployment air-gapped after a webhook or geocoder was added | done |
| `.local` hostname treated as local | mDNS names resolve on a LAN and any host can claim one, so `.local` is deliberately **not** in the local set | done |
| Malformed base URL bypassing classification | Hosts are validated as hostnames *before* being classified local; every dotless host is a container name, so `"not a url"` would otherwise pass | done |
| Backup that was never restored | Evidence is content-addressed, so `verify` compares recomputed digests against digests read from the **database** — not the backup's own inventory. A self-consistent archive missing evidence still fails | done |
| Restoring from an unverified backup | Verification is a precondition, not a warning | done |
| Model weight key stored beside its passphrase | Written 0600 into a named volume by a script running *inside* the container; never in the image or a bind mount | done |
| Security gate that never fails | `scripts/verify_security_gates.py` plants one problem per scanner and asserts rejection. A gate that cannot be made to fail is reported as a failure | done |
| An alert that parses but never fires | Every metric named in `alerts.yml` is checked against what the build actually emits; every `by (...)` against labels that exist | done (one gap recorded) |

## Deliberate non-goals

- **No TLS termination in-process.** Compose is single-host development; TLS
  belongs on the reverse proxy in production.
- **Per-IP rate limiting only.** No per-user limiting, so a shared NAT egress
  shares a bucket. Acceptable for a single-tenant lab; revisit if multi-tenant.
- **Tier-C disciplines are not modelled as AI analyzers** — see the honesty
  clause in `DESIGN_PLAN.md`. They are lab-attachment workflows, and building
  them as "AI modules" would be the simulation this project exists to avoid.
## The agent's authority, stated plainly

The agent is the component with the most capability and the least predictability, so
its limits are written here rather than left implicit:

- **It cannot write without a person.** Two tools are state-changing; both park
  frozen arguments and wait. Approve and reject are both audited, and an approval is
  bound to the exact arguments it approved — it cannot be replayed against different
  ones.
- **It cannot cite what does not exist.** Every citation resolves against a stored
  `analysis_results` row. Unsourced, cross-case, paraphrased and invented ids are
  rejected at write time, and a run that cannot source its claims records a refusal
  rather than an answer.
- **It cannot widen its own scope.** The case id comes from the run row, never from
  model arguments, and memory scope is a SQL predicate on (case, actor). A model that
  decides to go looking in another case finds nothing it can read.
- **It cannot reach the network.** Its one outbound call goes through the egress
  allowlist, which is empty by default.

The residual risk is not that the agent is jailbroken into doing something with
authority it should not have. It is that a *correctly scoped, correctly cited* agent
still produces a conclusion a human reads as a finding. That is what the tiering, the
disclaimers carried on every result, and the report's provenance appendix are for, and
no amount of prompt hardening substitutes for them.

## Still open

Recorded rather than left implicit, because an open item nobody wrote down is an open
item nobody is working on:

| Open item | Why it is still open |
|---|---|
| **Audit tail truncation is undetectable.** A prefix of the chain verifies fine, so an attacker who deletes recent entries leaves a valid-looking log. Fix is an external append-only anchor — periodically publishing the head hash somewhere the database cannot reach. | Needs a destination decision (a public timestamp service, a WORM bucket, a separate host). For an air-gapped deployment none of those is free. |
| **Per-IP rate limiting only.** No per-user limiting, so a shared NAT egress shares one bucket. | Acceptable for a single-tenant lab; revisit before multi-tenant. |
| **No TLS termination in-process.** Compose is single-host development. | TLS belongs on the reverse proxy; documented in `admin-guide.md`. |
| **`forensight_analysis_jobs_total` emits no samples until the first job runs**, because `prometheus_client` does not emit a labelled counter until `.labels()` is called. The alert watching it therefore sits on an empty vector on a fresh deployment. | Not fixable in the metric — pre-initialising every (module, outcome) pair means enumerating modules that plugins register at runtime. The honest options are the `absent()` alert variant or accepting the rule exists once the first job has run. |
| **No penetration test.** Bandit, Semgrep, pip-audit, Trivy and gitleaks run in CI and are themselves proven to fail on planted problems — which is evidence the scanners work, not evidence the application is free of findings. | Needs a human and a budget. |
| **Point-in-time recovery on Postgres is built and verified** (P10.B2): WAL archiving is configured and alerted on, base backups are taken with `pg_basebackup`, and recovery replays to a named restore point — checked against real rows either side of the target. **MinIO-side replication is still not configured.** | The `mc` client required to declare a remote target is no longer distributed: `minio/mc` and the Chainguard image both refuse to pull, and `dl.min.io` returns HTTP 410 because **open-source MinIO is archived and no longer maintained**. |
| **The evidence object store is AGPL-3.0 and unmaintained upstream.** MinIO ships no security advisories, so its durability and retention guarantees are a *load-bearing claim this system cannot evidence*. | AGPL-3.0 §6: distributing a container that runs AGPL MinIO conveys the licence and the corresponding source. ForenSight does not modify MinIO, so §13 does not attach to ForenSight's own code — but a deployment bundling the image cannot avoid the obligation short of replacing the object store. Recorded in `THIRD_PARTY_NOTICES.md`. |

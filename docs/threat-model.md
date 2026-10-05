# Threat model — ForenSight

Scope: the Phase 0–3 platform (auth, cases, evidence vault, custody, audit).
Not yet in scope: the agent (Phase 5), analyzers (6/9), reports (8), and the
frontend (7). Each of those adds its own attack surface and will need its own
pass.

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
Browser ──HTTPS──▶ Reverse proxy ──▶ FastAPI ──▶ Postgres / MinIO / Redis
                                         │
                                         └──▶ (Phase 5) Agent ──▶ Workers
```

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
| XSS in report/UI rendering | Deferred to Phase 7/8 — no HTML rendering exists yet | open |
| Agent prompt injection | Deferred to Phase 5 | open |
| Labelled data exfiltration by a user | Deferred to Phase 10 (egress control) | open |

## Deliberate non-goals

- **No TLS termination in-process.** Compose is single-host development; TLS
  belongs on the reverse proxy in production.
- **Per-IP rate limiting only.** No per-user limiting, so a shared NAT egress
  shares a bucket. Acceptable for a single-tenant lab; revisit if multi-tenant.
- **Tier-C disciplines are not modelled as AI analyzers** — see the honesty
  clause in `DESIGN_PLAN.md`. They are lab-attachment workflows, and building
  them as "AI modules" would be the simulation this project exists to avoid.
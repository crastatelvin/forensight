# Admin guide — ForenSight

## Roles

| Role | Read | Write | Sees |
|---|---|---|---|
| `admin` | everything | everything, incl. user creation | all cases |
| `officer` | owned + assigned | cases, evidence, findings, assignment | owned + assigned |
| `expert` | owned + assigned | analyses, findings, entities, timeline | owned + assigned |
| `reviewer` | everything | nothing | all cases |

Reviewer and admin both read every case — oversight is the point of the role.
Reviewer is held to read-only by RBAC, never by case scope.

Assignment is how collaboration happens: assigning a case to someone grants
them visibility of it. Without assignment, officers and experts cannot see each
other's work.

## User management

Create (admin only):

```bash
curl -X POST localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"email":"officer@example.com","password":"a-long-passphrase","role":"officer"}'
```

Passwords must be at least 12 characters and are stored as Argon2id hashes.

Revoke a session — revokes every refresh token for that user, forcing re-login:

```bash
curl -X POST localhost:8000/auth/logout -H "Authorization: Bearer $TOKEN"
```

Deactivating an account (`users.is_active = false`) takes effect on the user's
**next request**: the API revalidates the account on every authenticated call, so
an already-issued token stops working immediately rather than at expiry.

Password reset, deletion, and role-change endpoints are not built yet. Until
they are, promote or demote by updating `users.role` and revoking sessions:

```sql
UPDATE users SET role = 'reviewer' WHERE email = 'someone@example.com';
```

then have the user log out and back in, or revoke their refresh tokens:

```sql
UPDATE refresh_tokens SET revoked = true WHERE user_id = '<uuid>';
```

## Verifying the audit trail

```bash
curl -s localhost:8000/cases/audit \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.[0:5]'

curl -s localhost:8000/cases/audit/verify -H "Authorization: Bearer $ADMIN_TOKEN"
# {"valid": true, "entries": 812}
```

`valid: false` means a row was edited or the chain was forked. Investigate
before trusting any conclusion drawn from that period.

**Limitation worth stating to anyone relying on this:** the chain is
tamper-*evident*, not tamper-proof. There is no database-level protection
against `UPDATE`/`DELETE`, and deleting rows from the *end* of the log is not
detectable, because a shortened prefix still verifies. Anchor the head hash
externally if that matters for your accreditation.

## Evidence handling

- Objects are keyed by SHA-256 and held under COMPLIANCE object-lock for 10
  years. They cannot be overwritten or deleted within retention, including by
  an admin. That is the point.
- Uploads are MIME-sniffed, size-capped (200 MiB), and AV-scanned. Uploading
  something outside the allow-list is rejected.
- Every download re-verifies the hash against the recorded value. A mismatch
  returns `409` and writes an `evidence.integrity_fail` audit entry — check the
  audit log and the storage tier if you see one.
- Chain-of-custody is per-evidence and records actor, action, and time for
  uploads, dedupes, and every access.

## Operating honestly

Tier-A and Tier-B analyzer output (Phase 6/9) is investigative lead and visual
similarity — **not** courtroom individualization. Tier-C disciplines (DNA,
toxicology, pollen, entomology, odor, explosives) are not AI analyzers at all;
they are lab-attachment workflows, and the platform will not score them. See
the honesty clause in `DESIGN_PLAN.md`.

Do not let a report, a UI label, or a briefing overstate what a score means.

## Retention

Evidence retention is 10 years (COMPLIANCE, enforced by the object store). Case
and audit retention is not yet implemented — Phase 10. Nothing is currently
purged automatically.
## Egress

`EGRESS_ALLOWED_HOSTS` decides where this deployment may send case-related prompts.
**Empty permits no public host.** That is the air-gapped default, and it is
deliberate: a control that has to be switched on before it does anything is one most
deployments never switch on.

Local and container-network endpoints are always permitted — `localhost`,
`127.0.0.1`, `ollama`, `host.docker.internal` — because running a local model is what
an air-gapped deployment does, and requiring it on the list would teach operators the
control is noise.

To use a hosted provider, name it. Pin a scheme to require encryption:

```bash
# TLS only, for this host:
EGRESS_ALLOWED_HOSTS=https://api.openai.com
# Either scheme:
EGRESS_ALLOWED_HOSTS=api.openai.com,api.anthropic.com
```

`.local` is deliberately **not** treated as local. mDNS names resolve on a LAN and any
host on that LAN can claim one, so treating `printer.local` as "internal" would let it
receive every prompt — reached by a name that looks harmless.

When a provider is refused, the agent says **"unsupported by design"-style detail**:
the host, the setting to change, and why. It does not silently fall back to a local
model, because a run that quietly answered from somewhere other than the configured
provider is worse than one that refused.

## Backups

```bash
docker compose exec api python -m scripts.backup create /backups/$(date +%F)
docker compose exec api python -m scripts.backup verify /backups/2026-10-07
```

Evidence is content-addressed — the object key *is* the SHA-256 — so `verify` is
cryptographic rather than structural. For every row the database records it recomputes
the digest of the backed-up copy and compares it. A truncated object, a partial write
and a silently-substituted file all fail, and there is nothing to interpret.

Two consequences worth knowing:

- **Digests come from the database, not the backup's own inventory.** A backup that is
  internally consistent but quietly missing evidence still fails. That is the failure a
  file-exists check cannot catch.
- **Verify on a schedule, not after the incident.** `verify` exits non-zero and names
  each affected evidence record; "the backup is incomplete" is not actionable at 3am.

To restore, into a **named target**:

```bash
docker compose exec api python -m scripts.backup restore /backups/2026-10-07 \
  --target-database-url postgresql://forensight:...@localhost:5432/forensight_restored
```

There is deliberately **no in-place path and no `--force`**. A restore tool that can
overwrite the system it is protecting will eventually be aimed at it by someone in a
hurry. Verification is a precondition of restoring, not a warning printed afterwards.

One thing to know about `pg_restore`'s exit code: a newer `pg_dump` client against an
older server emits preamble statements the server rejects — a PG18 client writes
`SET transaction_timeout = 0`, which PG16 does not know — so it exits non-zero having
*applied the archive*. The tool therefore ignores that exit code and **verifies the
restore instead**: every digest the manifest records must be readable out of the
restored database. A stronger guarantee than any exit code, because it is a statement
about the data.

**Point-in-time recovery is now available** (`scripts/pitr.py`, procedure in
`docs/runbook.md`): WAL archiving is configured and alerted on, base backups are taken with
`pg_basebackup`, and recovery replays the archive to a named restore point and stops there.
Verified end to end on this deployment — see CHECKLIST.md under P10.B2.

Still not covered: **MinIO-side replication**, and **off-host replication of the WAL
archive**. The archive is a separate volume from `pgdata`, but a single host's loss takes
both.

## Shutdown and draining

The order is the feature, and it is visible from outside:

| Phase | What an observer sees |
|---|---|
| SIGTERM / Ctrl-C | `/readyz` → **503** with `{"draining": true}`. `/healthz` stays 200. |
| Grace period | New agent runs and analyses → **503** with a retry hint. Reads keep working. |
| Teardown | Sockets close, connections released. |

Three things that surprise people:

- **`/healthz` deliberately stays 200.** Liveness asks "is this worth restarting", and
  a draining process is not broken. Restarting it abandons the in-flight report the
  drain exists to finish, and the orchestrator then watches it die again.
- **Reads keep serving.** Refusing them would make a routine deploy look like an
  outage.
- **`dependencies` is empty during a drain**, not a list of failures. Nothing has been
  torn down yet, and a dashboard showing Postgres as failed during a deploy sends
  somebody to the wrong system.

`SHUTDOWN_GRACE_SECONDS` defaults to **0**. Set it deliberately: the right value is
however long your slowest operation takes, and a default either truncates reports or
stalls every rolling deploy.

## Constrained hosts

```bash
docker compose --profile android-dev up -d
```

CPU-only, and a **supported** deployment rather than a degraded one. Every analyzer
here is ONNX, so the CPU path runs the same pinned weights through `onnxruntime`
rather than a smaller substitute — a substitution would be a simulation, which is the
one thing this project refuses anywhere else.

GPU-only modules report `unavailable` through `/capabilities` **before** a job is
submitted, with the reason. If a capability list advertises a module this host cannot
run, the deployment is making a promise it cannot keep — which is a bug in the list,
not something to route around.

## Alerting

Rules live in `infra/observability/alerts.yml` and are loaded by `prometheus.yml`.

`ForenSightAPIDown` (`up == 0`) carries the observability: **every other rule is blind
while the target is down**, which is exactly when they are needed.

Two rules deliberately do not fire on things that look like failures:

- **`/readyz` is excluded from the error-rate rule.** It returns 503 *by design*
  whenever a dependency is down, so including it would fire for every database blip
  while the API was serving requests perfectly well.
- **`unsupported` and `not_applicable` are not failures.** A module declining an input
  it cannot honestly score did exactly what it should, and paging on it trains people
  to ignore pages.

Known gap: `forensight_analysis_jobs_total` emits no samples until the first job runs,
so the rule watching it sits on an empty vector on a fresh deployment. `up == 0` is
what covers that window.

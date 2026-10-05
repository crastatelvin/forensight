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
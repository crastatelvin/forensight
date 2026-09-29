# Secrets management

ForenSight never stores secrets in source. This document covers how secrets
are supplied today and the documented upgrade path.

## Today: environment / .env

- All secrets come from environment variables (see `.env.example`).
- `SECRET_KEY` is required; the API refuses to boot without it (test T0.4).
- `.env` is gitignored and never committed.
- The admin seed reads `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` from the
  environment; there are no default/hardcoded credentials (test T1.7).
- CI provides secrets via GitHub Actions secrets, not committed files.

## Recommended: SOPS + age (encrypted-at-rest in the repo)

For teams that want encrypted secrets in version control:

1. Generate an age key: `age-keygen -o keys.txt` (store the private key in a
   secure location / KMS, never in the repo).
2. Encrypt an env file: `sops --encrypt --age <public-key> .env > .env.enc`.
3. Commit only `.env.enc`. Decrypt at deploy time:
   `sops --decrypt .env.enc > .env`.
4. Rotate the `SECRET_KEY` and DB/MinIO credentials on a schedule; re-encrypt.

## Upgrade path: HashiCorp Vault (dynamic secrets)

For production at scale, migrate to Vault:

- Store static secrets in the KV v2 engine; issue short-lived DB credentials
  via the database secrets engine (no long-lived DB passwords).
- The API reads secrets at startup via the Vault Agent sidecar (templated to
  env) or the AppRole auth method.
- Because settings are centralized in `app/config.py`, switching the source
  (env -> Vault-injected env) requires no application code change.

## Rotation checklist

- `SECRET_KEY`: rotating invalidates existing access tokens (short-lived) and
  is safe; refresh tokens are opaque and stored hashed, unaffected by the key.
- DB / MinIO credentials: rotate via the provider, update the secret source,
  restart the API.

"""Application settings loaded from environment (Pydantic v2).

No secrets are hardcoded. The app refuses to boot if SECRET_KEY is unset
(enforced in `app.main`), satisfying test T0.4.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core
    app_name: str = "ForenSight"
    environment: str = Field(default="development")
    debug: bool = Field(default=False)

    # Security — no default; must be provided via env.
    secret_key: str = Field(default="")

    # Datastores (used by /readyz in later wiring)
    database_url: str = Field(
        default="postgresql+asyncpg://forensight:forensight@postgres:5432/forensight"
    )
    redis_url: str = Field(default="redis://redis:6379/0")

    minio_endpoint: str = Field(default="minio:9000")
    minio_access_key: str = Field(default="")
    minio_secret_key: str = Field(default="")
    minio_secure: bool = Field(default=False)

    # Capability flags — allow clean degradation on CPU-only / android-dev.
    gpu_available: bool = Field(default=False)

    # --- Auth / JWT (Phase 1) ---
    # Lifetimes in seconds.
    access_token_ttl: int = Field(default=900)  # 15 minutes
    refresh_token_ttl: int = Field(default=1209600)  # 14 days
    jwt_algorithm: str = Field(default="HS256")

    # Cookie flags for cookie-based auth flows.
    cookie_secure: bool = Field(default=True)  # override to False for local http

    # --- report signing (P8) ------------------------------------------------
    # Path to a PKCS#12 holding the report-signing key and certificate. There is no
    # "unsigned" mode: an unsigned report looks identical to a signed one until
    # someone relies on the difference, so without a key the report endpoint
    # refuses rather than quietly serving a PDF that carries no integrity claim.
    # Create one with scripts/provision_signer.py.
    report_signing_p12: str = Field(default="")
    report_signing_passphrase: str = Field(default="")

    # --- Long-term signature validation (PAdES-LT) ---
    #
    #: An RFC 3161 timestamp authority. Empty means PAdES baseline-B, which proves the
    #: bytes are unchanged but not *when* they were signed — so it stops establishing
    #: anything once the signing certificate expires.
    #:
    #: Setting this raises the signature to baseline-LT: a document timestamp plus the
    #: signing chain and any revocation data travel with the PDF, so a verifier can still
    #: validate it after expiry.
    #:
    #: **A TSA is only as trustworthy as its certificate.** One this deployment hosts
    #: itself (see `scripts/rfc3161_tsa.py`) proves the time against the lab's own key,
    #: which is the right answer air-gapped and the wrong answer if the verifier trusts
    #: nobody's key. The report states which authority was used and the verifier reports
    #: the timestamp separately from the signature, so the distinction is never lost.
    report_timestamp_url: str = Field(default="")

    #: Seconds to wait for the TSA. pyHanko's own default is 5, which is short for an
    #: authority that has to load a key and a serial file on first request.
    report_timestamp_timeout: float = Field(default=20.0, ge=1.0, le=300.0)

    #: Revocation material to embed, as comma-separated paths to DER or PEM CRLs.
    #:
    #: Optional, and the absence is not a failure: a private lab CA commonly publishes
    #: no CRL, and pyHanko soft-fails revocation by default. What it embeds without one
    #: is the signing chain, which is still the part a verifier needs most. A CRL here
    #: upgrades the document from "the chain travels with it" to "the chain was not
    #: revoked at the time", which is the substance of long-term validation.
    report_revocation_files: str = Field(default="")

    #: The timestamp authority's certificate, as a path to PEM or DER.
    #:
    #: **Required when `report_timestamp_url` is set**, and not merely helpful. Signing
    #: validates the countersignature's chain in order to embed it, so an authority whose
    #: certificate cannot be validated makes every signature fail — and a self-signed
    #: authority, which is the normal air-gapped case, cannot validate without being named
    #: as its own anchor here. Discovered by running it rather than by reading it.
    report_timestamp_ca: str = Field(default="")

    #: Extra trust anchors, as a path to a PEM or DER bundle, used when *collecting*
    #: validation material at signing time. Verification takes its roots from the
    #: caller — a report must be checkable by someone who has never heard of this
    #: deployment — so this affects signing only.
    report_signing_trust_roots: str = Field(default="")

    cookie_domain: str = Field(default="")
    cookie_samesite: Literal["lax", "strict", "none"] = Field(default="lax")

    # Browser origins allowed to call the API with credentials. Explicit list,
    # never "*" — credentialed CORS rejects the wildcard anyway.
    cors_origins: list[str] = Field(default=["http://localhost:3000", "http://127.0.0.1:3000"])

    # Rate limiting (Redis-backed; in-memory fallback for tests).
    rate_limit_auth_max: int = Field(default=5)  # attempts
    rate_limit_auth_window: int = Field(default=60)  # seconds
    rate_limit_mutation_max: int = Field(default=60)
    rate_limit_mutation_window: int = Field(default=60)
    #: Reads get their own, much larger budget. One case page renders seven reads
    #: in parallel, so the write budget was being spent on navigation and the UI
    #: returned 429 on ordinary use.
    rate_limit_read_max: int = Field(default=600)
    rate_limit_read_window: int = Field(default=60)

    # Initial admin seed (password MUST come from env; never hardcoded — T1.7).
    seed_admin_email: str = Field(default="")
    seed_admin_password: str = Field(default="")

    # --- Evidence / AV (Phase 2) ---
    # If set, evidence uploads are scanned via clamd at this host (TCP 3310).
    clamav_host: str = Field(default="")

    # --- Analysis workers (Phase 4) ---
    # Queue backend for analysis jobs. Left unset in dev so submission and
    # execution still work via the inline /run endpoint.
    queue_url: str = Field(default="")

    # --- Agent / LLM (Phase 5) ---
    # `ollama` for a local model, `cloud` for the hosted default. Switching is
    # configuration only; no code change (T5.1).
    llm_provider: str = Field(default="")
    llm_model: str = Field(default="")
    llm_base_url: str = Field(default="")
    #: Hosts this deployment may send case-related prompts to. Empty permits nothing
    #: beyond local and container-network endpoints, which is the air-gapped default
    #: and the reason this control needs no "mode" flag to be meaningful. See
    #: `app/agent/egress.py`.
    egress_allowed_hosts: str = Field(default="")
    #: Seconds to keep serving in-flight work after readiness drops.
    #:
    #: Zero by default, because the right value depends entirely on how long this
    #: deployment's slowest operation takes and a wrong default either truncates
    #: reports or stalls every rolling deploy. Set it deliberately.
    shutdown_grace_seconds: float = Field(default=0.0, ge=0.0, le=300.0)
    llm_api_key: str = Field(default="")
    #: Hard ceiling on tokens generated per agent step.
    #:
    #: **Measured, not guessed — and re-measured, because the first measurement was
    #: wrong.** On the constrained-host profile this project targets, `qwen2.5:3b`
    #: generates at roughly **5 tokens/second** on CPU: a 512-token completion takes
    #: **103 s**, not the 71 s an earlier note here claimed. Left unbounded — which is
    #: what an absent `max_tokens` means to the OpenAI chat completions API — a single
    #: agent step costs minutes of CPU and cannot fit any sensible HTTP timeout.
    #:
    #: 1024 comfortably fits a tool call plus prose while capping the worst case. It is a
    #: bound, not a target: the agent emits short structured output, so a truncated
    #: response would show up as an unusable tool call rather than as a quiet shortening.
    llm_max_tokens: int = Field(default=1024, ge=64, le=32768)
    #: HTTP timeout for one LLM call, which must outlast `llm_max_tokens` at the host's
    #: real generation rate or every agent run dies with `ModelError: timed out`.
    #:
    #: This was not configurable, and the hardcoded 60 s default was below what the
    #: host can produce: 1024 tokens at 5 tok/s is ~206 s. Found by running the agent
    #: against local Ollama — the test suite mocks the model, so it could not see it.
    #: 300 s covers the default token budget with headroom; raise it for a slower host.
    llm_timeout: float = Field(default=300.0, ge=1.0, le=1800.0)
    llm_embed_model: str = Field(default="")
    #: Must match the pgvector column width in migration 0006, and the width the
    #: embedding model actually returns. A mismatch is rejected on write rather
    #: than silently degrading recall.
    embed_dim: int = Field(default=1024, ge=8, le=8192)

    # --- Reverse proxy ---
    # uvicorn/gunicorn trust X-Forwarded-For from these sources only. Rate
    # limiting keys on client IP, so trusting everything ("*") lets any caller
    # forge an IP and evade limits entirely. Default to loopback.
    forwarded_allow_ips: str = Field(default="127.0.0.1")

    # Number of gunicorn worker processes in the container.
    web_concurrency: int = Field(default=2, ge=1, le=32)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()

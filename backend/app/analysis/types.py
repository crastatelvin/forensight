"""Core types for the analysis pipeline (Phase 4).

The contract every analyzer must satisfy. Deliberately a plain `Protocol` rather
than a base class or a registration framework: a module is a function that takes
evidence bytes plus validated params and returns a result dict. Three real
analyzers (Phase 6) do not justify a plugin framework.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Tier(StrEnum):
    """Evidence tier of a module's output, per DESIGN_PLAN section 2.

    The tier travels with every result so a score can never be read without its
    limitations. Tier B is explicitly "visual similarity / triage assist", never
    identity — surfacing a B score as an identification is the failure mode this
    whole tiering exists to prevent.
    """

    A = "A"
    B = "B"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    # Capability gating (T4.4): the host cannot run this module at all. Kept
    # distinct from FAILED because nothing went wrong — the work is simply
    # unsupported here, and reporting it as a failure would suggest a bug.
    UNSUPPORTED = "unsupported"


TIER_DISCLAIMER: dict[Tier, str] = {
    Tier.A: (
        "Investigative lead from a real open-source model with a published "
        "benchmark. Not courtroom individualization."
    ),
    Tier.B: ("Visual similarity / triage assist only. This is NOT an identity match."),
}


class AnalysisError(RuntimeError):
    """Raised by a module when analysis genuinely fails (job -> failed)."""


class UnsupportedCapability(AnalysisError):
    """Raised when a module cannot run on this host (job -> unsupported)."""


class ModelChecksumError(AnalysisError):
    """Raised when a model artifact does not match its pinned checksum.

    Distinct from UnsupportedCapability: a checksum mismatch means the artifact
    is wrong or tampered with, which is a refusal to proceed, never a fallback.
    """


@dataclass(frozen=True)
class ModuleSpec:
    """What a module declares about itself. Registered alongside the function.

    `model_version` is part of the idempotency key, so bumping it is what forces
    a recompute — that is the intended way to invalidate cached results.
    """

    name: str
    version: str
    tier: Tier
    #: Decision threshold for this module's score, always carried into the
    #: result so a score is never reported without the bar it was judged against.
    threshold: float
    #: Artifact to load before running, if any. None for pure-algorithm modules.
    model_key: str | None = None
    #: Expected SHA-256 of `model_key`. Verified before the module runs.
    model_sha256: str | None = None
    #: Input content types the module accepts, e.g. ("image/jpeg",).
    accepts: tuple[str, ...] = ()
    #: Params the module understands, for validation and documentation.
    params: dict[str, Any] = field(default_factory=dict)
    #: True when the module cannot run without a GPU.
    requires_gpu: bool = False


@runtime_checkable
class AnalysisModule(Protocol):
    """An analyzer: pure, deterministic, no I/O beyond the bytes it is handed.

    Determinism is the load-bearing property (admissibility depends on it), so a
    module must not use randomness, wall-clock time, or unversioned libraries
    that could change between runs. T6.6 adds a guard test for this.
    """

    spec: ModuleSpec

    def __call__(self, data: bytes, params: dict[str, Any]) -> dict[str, Any]:
        """Return at least `score` (float) and `detail` (dict)."""
        ...


def canonical_json(value: Any) -> str:
    """Stable JSON for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def idempotency_key(
    *,
    evidence_sha256: str,
    evidence_id: str,
    module_name: str,
    params: dict[str, Any],
    model_version: str,
) -> str:
    """hash(evidence_hash + evidence_id + module + params + model_version) — P4.B3.

    Every input that can change the output is in the key, so identical inputs
    always collapse to one computation and any change to them forces a new one.
    A UNIQUE constraint on this column is what makes that hold under a race
    (T4.6), rather than a check-then-insert that two workers can both pass.

    `evidence_id` is in the key alongside the content hash. The content hash
    alone is enough to reproduce a *score*, but a job is also a record inside a
    case: it shows up in that case's listing and audit log. Keying on content
    alone hands case B a job row that belongs to case A, which misreports where
    the work happened and leaks cross-case structure. Reusing one *result* across
    cases is a separate optimisation and belongs on the results table, not by
    sharing job rows between cases.
    """
    material = canonical_json(
        {
            "evidence_sha256": evidence_sha256,
            "evidence_id": evidence_id,
            "module": module_name,
            "params": params,
            "model_version": model_version,
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_result(spec: ModuleSpec, score: float, detail: dict[str, Any]) -> dict[str, Any]:
    """Assemble the canonical result payload (P4.B6).

    score, threshold, model_version and tier are injected here rather than
    trusted from the module, so no analyzer can emit a result that omits them.
    """
    return {
        "module": spec.name,
        "score": float(score),
        "threshold": spec.threshold,
        "model_version": spec.version,
        "tier": spec.tier.value,
        "disclaimer": TIER_DISCLAIMER[spec.tier],
        "detail": detail,
    }

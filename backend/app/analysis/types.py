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
    # The module ran, but its technique is undefined for this input: ELA on a
    # PNG, face matching with no face present. Recorded explicitly so "we could
    # not measure this" never renders as "we measured zero" (T6.2).
    NOT_APPLICABLE = "not_applicable"


#: Terminal job states. `not_applicable` is terminal without a result row.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        JobStatus.DONE.value,
        JobStatus.FAILED.value,
        JobStatus.UNSUPPORTED.value,
        JobStatus.NOT_APPLICABLE.value,
    }
)


TIER_DISCLAIMER: dict[Tier, str] = {
    Tier.A: (
        "Investigative lead from a real open-source model with a published "
        "benchmark. Not courtroom individualization."
    ),
    Tier.B: ("Visual similarity / triage assist only. This is NOT an identity match."),
}

#: What each tier means, for the UI.
#:
#: Served rather than written in the frontend. The interface had its own copy of
#: this, and the copy had drifted into saying the *opposite* of the backend on two of
#: the three tiers: it called Tier B a "corroborating signal" — which is how a
#: visual-similarity score gets read as a determination — and it called Tier C
#: "unvalidated, research value only", when Tier C is a deliberate refusal to score
#: at all (`app/analysis/lab_only.py`). Two hand-written copies of a safety label
#: will disagree, and the one that disagrees is the one a user reads.
#:
#: A tier is a statement about how far a number can be pushed. That statement belongs
#: to the code that assigns the tier, so the interface asks rather than asserts.
#: Keyed by string rather than by `Tier`, and deliberately so.
#:
#: `Tier` has no `C` member and must not grow one. Adding it would let a module
#: declare `tier=Tier.C` and emit a result carrying it — and a result with a tier is
#: a claim that something was measured. Tier C is the claim that nothing can be, so
#: it belongs to a technique in `lab_only.py`, never to a result row.
TIER_MEANING: dict[str, dict[str, str]] = {
    Tier.A.value: {
        "label": "Tier A — investigative lead",
        "meaning": (
            "A published, independently benchmarked method. A result above threshold "
            "is a legitimate lead. It is still not proof: no forensic technique is."
        ),
    },
    Tier.B.value: {
        "label": "Tier B — visual similarity, not identity",
        "meaning": (
            "A real measured signal that establishes similarity or triage relevance "
            "only. This is NOT an identity match and must never be reported as one. "
            "Use it to support or weaken a hypothesis formed from other evidence."
        ),
    },
    "C": {
        "label": "Tier C — lab only, not scored here",
        "meaning": (
            "A discipline this platform deliberately does not score. There is no "
            "module for it on any host and provisioning nothing will change that. "
            "Attach the accredited laboratory's report as evidence instead."
        ),
    },
}


class AnalysisError(RuntimeError):
    """Raised by a module when analysis genuinely fails (job -> failed)."""


class UnsupportedCapability(AnalysisError):
    """Raised when a module cannot run on this host (job -> unsupported)."""


class AnalysisNotApplicable(AnalysisError):
    """Raised when a technique is undefined for the given input (T6.2).

    ELA on a PNG, or face matching on an image with no face: there is no honest
    number to report. This exists so a module says "cannot measure" instead of
    returning 0.0, which a report would render as a clean negative result.
    """


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
    #: Additional pinned artifacts, keyed by the logical name the module will
    #: receive them under. Fetched as `<model_key>#<name>` and verified against
    #: the digest given here.
    #:
    #: A pipeline of two models is normal — face comparison is detect then embed —
    #: and a single `model_key` cannot express that. Bundling them into one archive
    #: would work but makes the pinned digest cover a container format as well as
    #: the weights, and a zip whose bytes depend on timestamps is not reproducible.
    #: Two named keys keep each artifact digest meaningful on its own.
    aux_models: dict[str, str] = field(default_factory=dict)
    #: Input content types the module accepts, e.g. ("image/jpeg",).
    accepts: tuple[str, ...] = ()
    #: Params the module understands, for validation and documentation.
    params: dict[str, Any] = field(default_factory=dict)
    #: True when the module cannot run without a GPU.
    requires_gpu: bool = False
    #: Overrides the tier's generic disclaimer. Tier says how much to trust the
    #: score; the disclaimer says what the technique can actually support. Those
    #: are different questions — the generic Tier B text is about identity
    #: matching, which is meaningless on an EXIF result — so a technique with
    #: its own limits states them itself.
    disclaimer: str | None = None


@runtime_checkable
class AnalysisModule(Protocol):
    """An analyzer: pure, deterministic, no I/O beyond the bytes it is handed.

    Determinism is the load-bearing property (admissibility depends on it), so a
    module must not use randomness, wall-clock time, or unversioned libraries
    that could change between runs. T6.6 adds a guard test for this.
    """

    spec: ModuleSpec

    def __call__(
        self,
        data: bytes,
        params: dict[str, Any],
        *,
        model_bytes: bytes | None = None,
        aux_models: dict[str, bytes] | None = None,
        reference_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        """Return at least `score` (float) and `detail` (dict).

        `model_bytes` is the checksum-verified artifact for modules that declare
        a `model_key`, and is None otherwise. It is keyword-only with a default so
        that a pure-algorithm module needs no knowledge of it, and so a module
        which does need weights cannot quietly fetch its own on some other route
        and bypass the pinned digest.

        `aux_models` carries the artifacts named in `spec.aux_models`, each
        verified against its own pinned digest.

        `reference_bytes` is a second evidence item from the same case, supplied
        only when `params["reference_evidence_id"]` is set. A comparison needs two
        things to compare; this is how the second one arrives, and the executor
        resolves it case-scoped so a job cannot name a reference from a case the
        caller cannot see.
        """
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
        "disclaimer": spec.disclaimer or TIER_DISCLAIMER[spec.tier],
        "detail": detail,
    }

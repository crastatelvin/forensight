"""Analysis pipeline: job submission, execution, and module registry (Phase 4).

Caching is handled at submission time (see `repository.submit_job`), not during
execution: identical work returns the existing job, so the module never runs
twice. That is why there is no cache lookup in `executor.execute_job`.
"""

from __future__ import annotations

from app.analysis.types import (
    AnalysisError,
    JobStatus,
    ModelChecksumError,
    ModuleSpec,
    Tier,
    UnsupportedCapability,
    build_result,
    canonical_json,
    idempotency_key,
)

__all__ = [
    "AnalysisError",
    "JobStatus",
    "ModelChecksumError",
    "ModuleSpec",
    "Tier",
    "UnsupportedCapability",
    "build_result",
    "canonical_json",
    "idempotency_key",
]

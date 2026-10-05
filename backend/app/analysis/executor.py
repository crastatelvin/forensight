"""Job execution: the code a worker runs (P4.B1/T4.2).

Deliberately framework-free. `execute_job` takes a session and a job and does the
work; how it got invoked — Celery, Dramatiq, RQ, or a synchronous call from a
test — is not its concern. That keeps the interesting logic (capability gating,
checksum verification, result assembly, failure recording) testable without a
running broker, which is where the real bugs live.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import registry
from app.analysis.models import AnalysisJob
from app.analysis.repository import (
    mark_done,
    mark_failed,
    mark_running,
    mark_unsupported,
)
from app.analysis.types import (
    AnalysisError,
    JobStatus,
    ModelChecksumError,
    UnsupportedCapability,
    build_result,
)
from app.config import Settings


class EvidenceNotFound(AnalysisError):
    """The job references evidence that no longer exists."""


async def execute_job(
    session: AsyncSession,
    job: AnalysisJob,
    *,
    settings: Settings,
    gpu_available: bool,
    model_store: Any | None = None,
    evidence_reader: Any | None = None,
) -> AnalysisJob:
    """Run one job to completion and record the outcome.

    Never raises for analysis failures: a failed module must leave a `failed` job
    behind and let the worker live to serve the next one (T4.2). It raises only
    when the caller needs to retry the *job transition* itself.

    `evidence_reader(evidence) -> bytes` is injectable so tests can supply
    fixture bytes without a live object store.
    """
    if job.status in (JobStatus.DONE.value, JobStatus.FAILED.value):
        # Already terminal: a redelivery of the same queue message.
        return job

    try:
        module = registry.get(job.module)
    except KeyError as exc:
        await mark_failed(session, job, str(exc))
        await session.commit()
        return job

    try:
        # Capability gating happens *before* any work, so an unsupported module
        # is never partially executed (T4.4).
        registry.check_capability(module, gpu_available=gpu_available)

        # Model checksum is verified before load (T4.B5/T4.3).
        if module.spec.model_key and module.spec.model_sha256:
            if model_store is None:
                raise ModelChecksumError(
                    f"module {job.module!r} needs a verified model but no store " f"was provided"
                )
            model_store.load_verified(
                key=module.spec.model_key, expected_sha256=module.spec.model_sha256
            )

        data = await _fetch_evidence(session, job, settings=settings, reader=evidence_reader)
        await mark_running(session, job)
        payload = await _invoke(module, data, job.params)
        # Re-wrap through build_result so the stored payload always carries the
        # mandatory fields even if a module returned a partial dict (P4.B6).
        payload = build_result(
            module.spec,
            score=payload["score"],
            detail=payload.get("detail", {}),
        )
        await mark_done(session, job, payload)
    except UnsupportedCapability as exc:
        await mark_unsupported(session, job, str(exc))
    except ModelChecksumError as exc:
        # Refusal, not a fallback: a wrong model cannot produce a defensible score.
        await mark_failed(session, job, str(exc))
    except AnalysisError as exc:
        await mark_failed(session, job, str(exc))
    except Exception as exc:  # noqa: BLE001 - a bad module must not kill the worker
        await mark_failed(session, job, f"{type(exc).__name__}: {exc}")

    await session.commit()
    return job


async def _invoke(module: Any, data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    """Call a module, awaiting it if it is a coroutine function.

    Modules are declared `def __call__` but may be written `async def`; this
    keeps both working without forcing every author to care.
    """
    import inspect

    outcome = module(data, params)
    if inspect.isawaitable(outcome):
        outcome = await outcome
    if not isinstance(outcome, dict) or "score" not in outcome:
        raise AnalysisError(
            f"module {module.spec.name!r} returned no 'score'; a result without a "
            f"score cannot be reported"
        )
    return outcome


async def _fetch_evidence(
    session: AsyncSession,
    job: AnalysisJob,
    *,
    settings: Settings,
    reader: Any | None = None,
) -> bytes:
    """Fetch evidence bytes for analysis, re-verifying the hash on read.

    Same rule as an analyst download (T2.1): if the stored bytes no longer match
    the recorded hash, the analysis must not run on them.
    """
    from app.domain.ingest import sha256_hex
    from app.domain.models import Evidence

    evidence = await session.get(Evidence, job.evidence_id)
    if evidence is None:
        raise EvidenceNotFound(f"evidence {job.evidence_id} no longer exists")

    data = reader(evidence) if reader is not None else _read_from_object_store(evidence, settings)
    if sha256_hex(data) != evidence.sha256:
        raise AnalysisError(f"evidence {evidence.id} failed hash verification; refusing to analyze")
    return data


def _read_from_object_store(evidence: Any, settings: Settings) -> bytes:
    from app.domain.storage import ObjectStore

    return ObjectStore(settings).get_evidence(evidence.storage_key)


__all__ = ["EvidenceNotFound", "execute_job"]

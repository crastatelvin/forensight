"""Job execution: the code a worker runs (P4.B1/T4.2).

Deliberately framework-free. `execute_job` takes a session and a job and does the
work; how it got invoked — Celery, Dramatiq, RQ, or a synchronous call from a
test — is not its concern. That keeps the interesting logic (capability gating,
checksum verification, result assembly, failure recording) testable without a
running broker, which is where the real bugs live.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import registry
from app.analysis.models import AnalysisJob
from app.analysis.repository import (
    mark_done,
    mark_failed,
    mark_not_applicable,
    mark_running,
    mark_unsupported,
)
from app.analysis.types import (
    TERMINAL_STATUSES,
    AnalysisError,
    AnalysisNotApplicable,
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
    if job.status in TERMINAL_STATUSES:
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

        # Model checksum is verified before load (T4.B5/T4.3), and the verified
        # bytes are handed to the module. Passing them matters: verifying and then
        # discarding them would let a module fetch its own weights by a different
        # route, so the check would attest to an artifact the module never used.
        model_bytes: bytes | None = None
        if module.spec.model_key and module.spec.model_sha256:
            if model_store is None:
                raise ModelChecksumError(
                    f"module {job.module!r} needs a verified model but no store " f"was provided"
                )
            model_bytes = model_store.load_verified(
                key=module.spec.model_key, expected_sha256=module.spec.model_sha256
            )

        # Additional pinned artifacts (e.g. a detector alongside an embedder),
        # each verified against its own digest.
        aux_models: dict[str, bytes] = {}
        for aux_name, aux_sha in module.spec.aux_models.items():
            if model_store is None:
                raise ModelChecksumError(
                    f"module {job.module!r} needs pinned model {aux_name!r} but no store "
                    f"was provided"
                )
            aux_models[aux_name] = model_store.load_verified(
                key=f"{module.spec.model_key}#{aux_name}", expected_sha256=aux_sha
            )

        data = await _fetch_evidence(session, job, settings=settings, reader=evidence_reader)
        reference_bytes = await _fetch_reference(
            session, job, settings=settings, reader=evidence_reader
        )
        await mark_running(session, job)
        payload = await _invoke(
            module,
            data,
            job.params,
            model_bytes=model_bytes,
            aux_models=aux_models,
            reference_bytes=reference_bytes,
        )
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
    except AnalysisNotApplicable as exc:
        await mark_not_applicable(session, job, str(exc))
    except ModelChecksumError as exc:
        # Refusal, not a fallback: a wrong model cannot produce a defensible score.
        await mark_failed(session, job, str(exc))
    except AnalysisError as exc:
        await mark_failed(session, job, str(exc))
    except Exception as exc:  # noqa: BLE001 - a bad module must not kill the worker
        await mark_failed(session, job, f"{type(exc).__name__}: {exc}")

    await session.commit()
    return job


async def _fetch_reference(
    session: AsyncSession,
    job: AnalysisJob,
    *,
    settings: Settings,
    reader: Any | None = None,
) -> bytes | None:
    """Load the second image for a comparison, scoped to the job's own case.

    `params["reference_evidence_id"]` names the image to compare against. It is
    resolved with an explicit `case_id` predicate rather than by primary key
    alone: a job in case A must not be able to name evidence from case B, and
    without that predicate the id would be a cross-case read.

    The bytes are hash-verified on the same rule as the primary evidence — the
    comparison must not run against bytes that no longer match what was recorded.

    Returns None when no reference was requested, which is not an error: most
    modules compare against nothing.
    """
    reference_id = job.params.get("reference_evidence_id") if job.params else None
    if not reference_id:
        return None
    try:
        wanted = uuid.UUID(str(reference_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise AnalysisError(
            f"reference_evidence_id {reference_id!r} is not a valid evidence id"
        ) from exc

    from app.domain.ingest import sha256_hex
    from app.domain.models import Evidence

    found = await session.execute(
        select(Evidence).where(Evidence.id == wanted, Evidence.case_id == job.case_id)
    )
    evidence = found.scalar_one_or_none()
    if evidence is None:
        raise AnalysisError(f"reference evidence {wanted} does not exist in case {job.case_id}")

    data = reader(evidence) if reader is not None else _read_from_object_store(evidence, settings)
    if sha256_hex(data) != evidence.sha256:
        raise AnalysisError(
            f"reference evidence {evidence.id} failed hash verification; refusing to compare"
        )
    return data


async def _invoke(
    module: Any,
    data: bytes,
    params: dict[str, Any],
    *,
    model_bytes: bytes | None = None,
    aux_models: dict[str, bytes] | None = None,
    reference_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Call a module, awaiting it if it is a coroutine function.

    Modules are declared `def __call__` but may be written `async def`; this
    keeps both working without forcing every author to care.

    The optional keywords are passed only to modules whose `__call__` declares
    them, so the two-argument signature stays valid and existing modules
    (byte-entropy, image-ela, image-exif, object-detect) need no change.
    """
    import inspect

    accepted = inspect.signature(module.__call__).parameters
    extra: dict[str, Any] = {}
    if "model_bytes" in accepted:
        extra["model_bytes"] = model_bytes
    if "aux_models" in accepted:
        extra["aux_models"] = aux_models or {}
    if "reference_bytes" in accepted:
        extra["reference_bytes"] = reference_bytes

    outcome = module(data, params, **extra)
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

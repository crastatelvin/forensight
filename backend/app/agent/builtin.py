"""Built-in agent tools (P5.B5/B6).

The tool set the plan specifies, minus what cannot honestly exist yet:
`generate_report` needs a signed-PDF renderer (Phase 8), `kb_search` needs a
forensic knowledge base that was never built, and `compute_geo` needs PostGIS
geospatial analysis (Phase 9). Rather than ship stubs that pretend to work they
are simply absent, and `GET /agent/tools` publishes the catalogue so the gap is
visible rather than silently missing.

`run_analysis` delegates to the Phase 4 pipeline instead of recomputing anything,
which is how the agent inherits its idempotency and caching for free (T5.8).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from pydantic import BaseModel, Field

from app.agent.tools import Tool, ToolContext, ToolError, define_tool


class RunAnalysisArgs(BaseModel):
    evidence_id: uuid.UUID = Field(description="Evidence to analyse")
    module: str = Field(default="byte-entropy", description="Analyzer module name", max_length=64)
    params: dict[str, Any] = Field(default_factory=dict, description="Module parameters")


@define_tool(
    name="run_analysis",
    description=(
        "Analyze one evidence item with one analyzer. Returns the score, threshold "
        "and model version, which are citable. Returns result_available=false with "
        "a reason when the analysis could not be performed. Call get_case first: "
        "evidence_id must be copied exactly from its 'evidence' list."
    ),
    args_model=RunAnalysisArgs,
)
async def run_analysis(ctx: ToolContext, args: RunAnalysisArgs) -> dict[str, Any]:
    from app.analysis import registry
    from app.analysis.repository import get_result_for_key, submit_job
    from app.analysis.types import idempotency_key
    from app.domain.models import Evidence

    module = registry.get(args.module)
    evidence = await ctx.session.get(Evidence, args.evidence_id)
    if evidence is None:
        raise ToolError(f"evidence {args.evidence_id} not found")
    # Case scope comes from the run, never from the model's arguments. This is
    # the boundary a jailbroken model cannot cross.
    if evidence.case_id != ctx.case_id:
        raise ToolError("evidence belongs to another case")

    gpu = bool(getattr(ctx.settings, "gpu_available", False))
    if module.spec.requires_gpu and not gpu:
        raise ToolError(f"module {module.spec.name} requires a GPU; this host has none")

    key = idempotency_key(
        evidence_sha256=evidence.sha256,
        evidence_id=str(evidence.id),
        module_name=module.spec.name,
        params=args.params,
        model_version=module.spec.version,
    )
    existing = await get_result_for_key(ctx.session, key)
    if existing is not None:
        # T5.8: identical work already done. Report it rather than requeueing.
        #
        # Shaped like the freshly-computed branch below, so a caller cannot tell
        # the two apart by inspecting the response: a cache hit is as citable as a
        # cold run, and a shape difference would invite a `cached` check to be read
        # as "not a result".
        return {
            "cached": True,
            "result_available": True,
            "job_id": str(existing.job_id),
            "status": "done",
            "result": {
                "result_id": str(existing.id),
                "analyzer": existing.module,
                "score": existing.score,
                "threshold": existing.threshold,
                "model_version": existing.model_version,
                "tier": existing.tier,
            },
            "note": "identical work was already performed; the stored result is citable",
        }

    job, created = await submit_job(
        ctx.session,
        case_id=ctx.case_id,
        evidence_id=evidence.id,
        evidence_sha256=evidence.sha256,
        module=module.spec.name,
        module_version=module.spec.version,
        params=args.params,
        requested_by=ctx.actor_id,
        gpu_available=gpu,
    )
    from app.analysis.queue import enqueue_analysis_job

    queued = enqueue_analysis_job(job.id, settings=ctx.settings) if created else False

    # A tool result must tell the model what it can do *next*. Returning
    # "analysis is asynchronous; re-read results before citing" was an
    # instruction the loop could not carry out: the only tool that shows results is
    # `search_evidence`, which the model had to guess to call, and a tool-call turn
    # does not get a chance to be retried. Worse, `search_evidence` reads whatever
    # exists at the time it is called, so a result produced after it ran is
    # invisible for the rest of the run.
    #
    # So: run it here when there is no worker. That is not a simulation -- it is the
    # same `execute_job` the Dramatiq worker calls, with the same model
    # verification and the same refusal statuses. It only replaces the transport.
    # With a broker configured, the job is queued and the tool says plainly that the
    # result is not available yet.
    if queued:
        return {
            "cached": False,
            "job_id": str(job.id),
            "status": job.status,
            "module": module.spec.name,
            "result_available": False,
            "note": "queued for a worker; the result is not available yet, so do "
            "not cite this analysis. Report what the case already contains.",
        }

    # No broker: execute inline so the run can finish with a sourced result.
    from app.analysis.executor import execute_job

    executed = await execute_job(
        ctx.session,
        job,
        settings=ctx.settings,
        gpu_available=gpu,
        model_store=_model_store(ctx.settings),
    )

    if executed.status == "done":
        stored = await get_result_for_key(ctx.session, key)
        return {
            "cached": False,
            "job_id": str(job.id),
            "status": executed.status,
            "module": module.spec.name,
            "result_available": True,
            "result": (
                {
                    "result_id": str(stored.id),
                    "analyzer": stored.module,
                    "score": stored.score,
                    "threshold": stored.threshold,
                    "model_version": stored.model_version,
                    "tier": stored.tier,
                }
                if stored is not None
                else None
            ),
            "note": "analysis complete; the result above is citable",
        }

    # Refusals are data. The model needs to know *why* there is no number, or it
    # will either invent one or repeat the same call.
    return {
        "cached": False,
        "job_id": str(job.id),
        "status": executed.status,
        "module": module.spec.name,
        "result_available": False,
        "reason": executed.error or executed.unsupported_reason,
        "note": (
            "no result was produced; report that the analysis did not apply or "
            "failed rather than citing a score"
        ),
    }


def _model_store(settings: Any) -> Any:
    """A ModelStore for the executor to verify pinned weights against."""
    from app.analysis.models_registry import ModelStore

    return ModelStore(settings)


class GetCaseArgs(BaseModel):
    include_evidence: bool = Field(default=True)


@define_tool(
    name="get_case",
    description="Read the case this run is scoped to.",
    args_model=GetCaseArgs,
)
async def get_case(ctx: ToolContext, args: GetCaseArgs) -> dict[str, Any]:
    from app.domain.models import Case
    from app.domain.repository import list_case_evidence

    case = await ctx.session.get(Case, ctx.case_id)
    if case is None:
        raise ToolError("case not found")
    out: dict[str, Any] = {
        "number": case.number,
        "title": case.title,
        "description": case.description,
        "status": case.status,
    }
    if args.include_evidence:
        rows = await list_case_evidence(ctx.session, ctx.case_id, limit=100)
        out["evidence"] = [
            {
                "id": str(e.id),
                "filename": e.filename,
                "sha256": e.sha256,
                "content_type": e.content_type,
                "size_bytes": e.size_bytes,
            }
            for e in rows
        ]
    return out


class SearchEvidenceArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


@define_tool(
    name="search_evidence",
    description="List this case's evidence alongside its analyzer results.",
    args_model=SearchEvidenceArgs,
)
async def search_evidence(ctx: ToolContext, args: SearchEvidenceArgs) -> dict[str, Any]:
    from app.analysis.repository import list_results_for_evidence
    from app.domain.repository import list_case_evidence

    rows = await list_case_evidence(ctx.session, ctx.case_id, limit=100)
    out: list[dict[str, Any]] = []
    for e in rows[: args.limit]:
        results = await list_results_for_evidence(ctx.session, e.id, limit=10)
        out.append(
            {
                "evidence_id": str(e.id),
                "filename": e.filename,
                "sha256": e.sha256,
                "results": [
                    {
                        "result_id": str(r.id),
                        "analyzer": r.module,
                        "score": r.score,
                        "threshold": r.threshold,
                        "model_version": r.model_version,
                        "tier": r.tier,
                    }
                    for r in results
                ],
            }
        )
    return {"evidence": out}


class RecallMemoryArgs(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=50)


@define_tool(
    name="recall_memory",
    description="Recall earlier notes. Scoped to this case and actor only.",
    args_model=RecallMemoryArgs,
)
async def recall_memory(ctx: ToolContext, args: RecallMemoryArgs) -> dict[str, Any]:
    from app.agent.repository import recall_memory as _recall

    model = ctx.services.get("model")
    vector: list[float] = []
    if model is not None:
        try:
            vector = model.embed([args.query])[0]
        except Exception:  # noqa: BLE001 - recall degrades, it does not fail
            vector = []
    rows = await _recall(
        ctx.session,
        case_id=ctx.case_id,
        actor_id=ctx.actor_id,
        query_embedding=vector,
        limit=args.limit,
    )
    return {"memories": rows}


class WriteMemoryArgs(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    kind: str = Field(default="semantic", pattern="^(episodic|semantic)$")


@define_tool(
    name="write_memory",
    description="Store a durable note for this case.",
    args_model=WriteMemoryArgs,
    state_changing=True,
)
async def write_memory(ctx: ToolContext, args: WriteMemoryArgs) -> dict[str, Any]:
    from app.agent.repository import write_memory as _write
    from app.agent.security import sanitize

    # Memory content can come from evidence, so sanitise on the way in: the next
    # run's recall must not hand an injection payload back to the model as if it
    # were context.
    clean, findings = sanitize(args.content)
    model = ctx.services.get("model")
    vector: list[float] = []
    if model is not None:
        try:
            vector = model.embed([clean])[0]
        except Exception:  # noqa: BLE001
            vector = []
    row = await _write(
        ctx.session,
        case_id=ctx.case_id,
        actor_id=ctx.actor_id,
        kind=args.kind,
        content=clean,
        embedding=vector,
        injection_findings=findings,
        run_id=uuid.UUID(ctx.run_id),
    )
    return {"memory_id": str(row.id), "sanitised": findings}


class UpdateFindingArgs(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(default="", max_length=20000)
    #: Analyzer result ids this finding rests on, as recorded in `detail.result_id`
    #: by `run_analysis` or `list_results`.
    #:
    #: Optional, and a finding without it is recorded rather than refused. The model
    #: has to be able to write down an observation before it can find the results
    #: backing it. What must not happen is the opposite: a citation that does not
    #: resolve being quietly dropped, leaving a finding that looks sourced and is
    #: not. Unresolvable ids come back in `rejected_citations` so the agent can see
    #: which of its references missed and try again.
    citations: list[str] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "Ids of the analyzer results this finding rests on. Get them from "
            "run_analysis or list_results. Omit if the finding rests on judgement "
            "rather than analysis -- it will be recorded as unsourced."
        ),
    )


@define_tool(
    name="update_case_finding",
    description=(
        "Record a finding on the case. Changes record state, so it requires "
        "human approval before it is applied."
    ),
    args_model=UpdateFindingArgs,
    state_changing=True,
)
async def update_case_finding(ctx: ToolContext, args: UpdateFindingArgs) -> dict[str, Any]:
    from app.agent.repository import record_tool_call  # noqa: F401  (audit parity)
    from app.analysis.repository import list_results_for_case
    from app.domain.repository import append_audit
    from app.domain.workflow_repo import cite_results, create_finding

    finding = await create_finding(
        ctx.session,
        case_id=ctx.case_id,
        title=args.title,
        body=args.body,
        created_by=ctx.actor_id,
    )

    # Which results this finding rests on — the thing a signed report has to print.
    # Previously only the *number* of results on the case was recorded, which said
    # nothing about this finding's warrant and could not be reported on at all.
    parsed: list[uuid.UUID] = []
    unparseable: list[str] = []
    for raw in args.citations:
        try:
            parsed.append(uuid.UUID(raw))
        except (ValueError, AttributeError, TypeError):
            # Invented ids are the common case, not an edge case: a small model given
            # "cite your sources" will produce `evidence_id="attached_image"`. Kept as
            # text and reported back rather than raising, so the agent learns which
            # reference failed instead of the whole call failing.
            unparseable.append(raw[:80])

    citations, rejected = await cite_results(
        ctx.session, finding=finding, case_id=ctx.case_id, result_ids=parsed
    )

    results = await list_results_for_case(ctx.session, ctx.case_id, limit=100)
    await append_audit(
        ctx.session,
        actor_id=ctx.actor_id,
        action="agent.finding.create",
        target=str(finding.id),
        payload={
            "run_id": ctx.run_id,
            "title": finding.title,
            "cited_result_ids": [str(c.result_id) for c in citations],
            "rejected_citation_ids": [str(r) for r in rejected],
            "unparseable_citations": unparseable,
            "available_results": len(results),
        },
    )
    return {
        "finding_id": str(finding.id),
        "status": finding.status,
        "cited_result_ids": [str(c.result_id) for c in citations],
        # Reported rather than swallowed: a citation that did not resolve must not
        # leave the finding looking sourced.
        "rejected_citation_ids": [str(r) for r in rejected],
        "unparseable_citations": unparseable,
        "available_results": len(results),
        "sourced": bool(citations),
        **(
            {}
            if citations
            else {
                "note": (
                    "This finding cites no analyzer result and is recorded as "
                    "unsourced. It will appear in any signed report marked NOT "
                    "SOURCED. Run an analyzer and pass its result id to make it "
                    "sourced."
                )
            }
        ),
    }


class QueryCasesArgs(BaseModel):
    q: str = Field(min_length=1, max_length=256)
    limit: int = Field(default=20, ge=1, le=100)


@define_tool(
    name="query_cases",
    description="Search case metadata the caller may see.",
    args_model=QueryCasesArgs,
)
async def query_cases(ctx: ToolContext, args: QueryCasesArgs) -> dict[str, Any]:
    from app.domain.repository import search_cases

    # Scoped to the run's case. Cross-case search is an API concern with its own
    # role checks; giving the agent a wider view than its own case would put the
    # scoping work in the prompt instead of in SQL.
    rows = await search_cases(ctx.session, args.q, limit=args.limit)
    visible = [
        {
            "id": str(c.id),
            "number": c.number,
            "title": c.title,
            "status": c.status,
            "is_current_case": c.id == ctx.case_id,
        }
        for c in rows
        if c.id == ctx.case_id
    ]
    return {"cases": visible}


def register_all() -> None:
    """Register the built-in tools. Idempotent: re-running is a no-op."""
    from app.agent.tools import register, registry

    builtins = (
        run_analysis,
        get_case,
        search_evidence,
        recall_memory,
        write_memory,
        update_case_finding,
        query_cases,
    )
    for tool in builtins:
        # define_tool attaches `name`; the cast tells mypy the callable is a Tool.
        typed = cast(Tool, tool)
        if typed.name not in registry():
            register(typed)

"""Agent HTTP surface (Phase 5).

Every route is case-scoped through `_scoped_case`, so an agent run is started
inside a case the caller may actually see. `run_stream` is SSE rather than
WebSocket: the traffic is one-directional after the run starts, SSE survives
proxies, and it needs no extra dependency.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import AgentGraph, load_state
from app.agent.llm import ModelError, resolve_model
from app.agent.repository import (
    AgentRun,
    create_run,
    get_run,
    latest_checkpoint,
    list_checkpoints,
    list_runs,
    list_tool_calls,
    update_run,
)
from app.agent.security import InjectionDetected, validate_operator_instruction
from app.agent.tools import tool_catalog
from app.agent.types import AgentState, Citation, Decision, RunStatus, Step
from app.auth.rbac import CurrentUser, get_current_user, require_roles
from app.auth.roles import MUTATING_ROLES
from app.config import Settings, get_settings
from app.db import get_session
from app.domain.access import visible_case
from app.domain.models import Case
from app.domain.repository import append_audit

router = APIRouter(prefix="/agent", tags=["agent"])

_MAX_PAGE = 200


async def _scoped_case(session: AsyncSession, case_id: uuid.UUID, user: CurrentUser) -> Case:
    case = await visible_case(session, case_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def _scoped_run(session: AsyncSession, run_id: uuid.UUID, user: CurrentUser) -> AgentRun:
    run = await get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    case = await visible_case(session, run.case_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


def _model(settings: Settings) -> Any:  # returns the ChatModel protocol
    try:
        return resolve_model(settings)
    except ModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _model_name(settings: Settings) -> str:
    """The configured model, named without building a client for it.

    `start_run` records which model produced a run — real provenance — but no
    longer *uses* the model, since the SSE stream drives the graph. Resolving one
    anyway made the endpoint 503 whenever no LLM was configured, which is wrong: a
    deployment can legitimately create and checkpoint a run while the model service
    is down, and the refusal should come from the call that actually needs it.

    Falls back to the configured name when the provider cannot be resolved, so a
    run is never recorded against an unnamed model.
    """
    try:
        return resolve_model(settings).name
    except ModelError:
        configured = str(getattr(settings, "llm_model", "") or "").strip()
        return configured or "unconfigured"


# --- schemas ---


class StartRunRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=4000)
    thread_id: str = Field(default="", max_length=128)


class DecisionRequest(BaseModel):
    action_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=2000)


class RunRead(BaseModel):
    run_id: str
    case_id: str
    thread_id: str
    status: str
    step: str
    objective: str
    report: str
    refusal: str
    error: str
    model: str
    pending_action_id: str = ""
    pending_tool: str = ""
    pending_args: dict[str, Any] = {}
    created_at: str
    updated_at: str


class ToolCallRead(BaseModel):
    tool: str
    args: dict[str, Any]
    args_hash: str
    result_hash: str
    ok: bool
    error: str
    approved_action_id: str
    created_at: str


class CheckpointRead(BaseModel):
    step: str
    created_at: str


def _citation(c: Citation) -> dict[str, Any]:
    """A citation, serialised via its own `as_dict`.

    Delegated rather than field-by-field: hand-writing this is how it came to be
    wrong the first time (it read `module`, `locator` and `quote`, none of which
    exist on `Citation`), and `as_dict` is the canonical shape the report builder
    already uses. One definition of what a citation looks like, not two.
    """
    return c.as_dict()


def _run_payload(run: Any, state: AgentState | None) -> dict[str, Any]:
    """A run, plus everything in the state that a client has to be shown.

    The provenance fields are the reason this function is shaped this way. The
    agent's contract is that it declines rather than asserting what it cannot
    source -- and that contract is worthless to a reader if the claims, their
    citations and the tool calls behind them are not in the payload. They were
    stored in the checkpoint the entire time and simply never serialised, so the
    only way to check whether a claim was sourced was to query the database
    directly. A client that renders the agent's prose without showing its sources
    also quietly undoes the contract, which is the whole reason it exists.

    A claim with an empty `citations` list is emitted *as such*: the UI has to be
    able to show that an assertion was unsourced, rather than have the claim
    vanish and leave the reader assuming everything shown is backed.

    Every list field is always present, never omitted, so a client never has to
    distinguish "no claims" from "an older payload that did not have claims".
    """
    pending = state.pending if state else None
    return {
        "run_id": str(run.id),
        "case_id": str(run.case_id),
        "thread_id": run.thread_id,
        "status": run.status,
        "step": run.step,
        "objective": run.objective,
        "report": run.report,
        "refusal": run.refusal,
        "error": run.error,
        "model": run.model,
        "pending_action_id": pending.action_id if pending else "",
        "pending_tool": pending.tool if pending else "",
        "pending_args": pending.args if pending else {},
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "claims": [
            {"text": claim.text, "citations": [_citation(c) for c in claim.citations]}
            for claim in (state.claims if state else [])
        ],
        "citations": [_citation(c) for c in (state.citations if state else [])],
        "tool_calls": [
            {
                "tool": call.name,
                "call_id": call.call_id,
                "args": call.args,
                "args_hash": call.args_hash(),
                # Three states, not two. `ok` is not on ToolCall -- a call's
                # outcome lives on its result -- so it is joined from the matching
                # result to keep the audit row and the displayed status from
                # disagreeing. A call whose action is *parked for approval* has no
                # result yet, and reporting that as `ok: false` would call it a
                # failure: the agent has not done anything wrong, it is waiting for
                # a person. `pending` is the truth, and a client that shows "failed"
                # for an action still awaiting a decision is stating a falsehood
                # about the audit trail.
                "status": _call_status(state, call.call_id),
                "ok": _result_ok(state, call.call_id),
                "error": _result_error(state, call.call_id),
                "result_hash": _result_hash(state, call.call_id),
                # From the *result*, not the current pending action: once the run
                # continues, the pending action is cleared, and a call that was
                # applied because a human approved it has to stay distinguishable
                # from one applied directly. That distinction is the whole point of
                # the HITL gate, so it cannot depend on state that is about to change.
                "approved_action_id": _approved_for(state, call.call_id),
            }
            for call in (state.tool_calls if state else [])
        ],
        "messages": [
            {"role": m.get("role", ""), "content": m.get("content", "")}
            for m in (list(state.messages) if state else [])
            if isinstance(m, dict)
        ],
    }


def _result(state: AgentState | None, call_id: str) -> Any:
    if state is None:
        return None
    for result in state.tool_results:
        if result.call_id == call_id:
            return result
    return None


def _call_status(state: AgentState | None, call_id: str) -> str:
    """`ok`, `failed` or `pending` for one tool call.

    `pending` is the case that matters. A parked action has produced no result, and
    the previous join rendered that as `ok: false` -- so the console displayed a
    waiting action as a failed one, which is a false statement about an audit trail
    and the sort of thing that erodes trust in everything else the log says.
    """
    result = _result(state, call_id)
    if result is not None:
        return "ok" if result.ok else "failed"
    if state is not None and state.pending is not None:
        return "pending"
    return "failed"


def _result_ok(state: AgentState | None, call_id: str) -> bool:
    result = _result(state, call_id)
    # A call with no recorded result is not a success. Defaulting it to True would
    # render an unexecuted call as a clean one.
    return bool(result.ok) if result is not None else False


def _result_error(state: AgentState | None, call_id: str) -> str:
    result = _result(state, call_id)
    if result is not None:
        return str(result.error)
    if state is not None and state.pending is not None:
        return "awaiting human approval; not run yet"
    return "no result was recorded"


def _approved_for(state: AgentState | None, call_id: str) -> str:
    """The human decision that authorised this call, if any."""
    result = _result(state, call_id)
    return result.approved_action_id if result is not None else ""


def _result_hash(state: AgentState | None, call_id: str) -> str:
    result = _result(state, call_id)
    return result.result_hash if result is not None else ""


# --- routes ---


@router.get("/tools")
async def list_agent_tools(
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    """The tool catalogue the agent may call.

    Published rather than hidden so a client can see exactly which tools exist
    and which need approval. Tools for Phase 8/9 (report generation, knowledge
    base, geospatial) are absent, not stubbed.
    """
    return tool_catalog()


@router.get("/cases/{case_id}/runs")
async def list_case_runs(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    """Every agent run on this case, newest first, with its provenance.

    Scoped by the same visibility rule as the case itself, so a run on someone
    else's case is a 404 rather than a filtered list — a partial list would still
    tell you that runs exist.

    Each entry carries the full run payload including claims, citations and tool
    calls, so a report built from this does not need a second round trip per run.
    """
    case = await _scoped_case(session, case_id, user)
    runs = await list_runs(session, case.id, limit=limit)
    return [_run_payload(run, await load_state(session, run.id)) for run in runs]


@router.post("/cases/{case_id}/runs", status_code=201)
async def start_run(
    case_id: uuid.UUID,
    payload: StartRunRequest,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Start an investigative run. Returns immediately; the run may pause for
    human approval."""
    case = await _scoped_case(session, case_id, user)
    try:
        # An operator's objective is trusted input, so an injection shape here is
        # an error rather than something to sanitise away.
        objective = validate_operator_instruction(payload.objective)
    except InjectionDetected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    thread_id = payload.thread_id or f"case-{case.id}"
    run = await create_run(
        session,
        case_id=case.id,
        thread_id=thread_id,
        objective=objective,
        actor_id=uuid.UUID(user.id),
        model=_model_name(settings),
    )
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="agent.run.start",
        target=str(run.id),
        payload={
            "case_id": str(case.id),
            "model": _model_name(settings),
            "thread_id": thread_id,
        },
    )
    await session.commit()

    state = AgentState(
        run_id=str(run.id),
        case_id=str(case.id),
        thread_id=thread_id,
        objective=objective,
    )
    # The first step and a checkpoint only -- no model is resolved and none is
    # needed. The SSE stream drives the graph, and running it inline meant this
    # request blocked for every LLM round trip before answering, which is precisely
    # what the docstring above says this endpoint does not do.
    await AgentGraph(
        session=session,
        settings=settings,
        run=run,
        case_number=case.number,
        case_title=case.title,
    ).begin(state)
    return _run_payload(run, await load_state(session, run.id))


@router.get("/runs/{run_id}")
async def read_run(
    run_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    run = await _scoped_run(session, run_id, user)
    state = await load_state(session, run.id)
    return _run_payload(run, state)


@router.get("/runs/{run_id}/tool-calls")
async def read_tool_calls(
    run_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ToolCallRead]:
    """Every tool call for a run, including rejected ones (T5.2)."""
    run = await _scoped_run(session, run_id, user)
    rows = await list_tool_calls(session, run.id)
    return [
        ToolCallRead(
            tool=r.tool,
            args=r.args,
            args_hash=r.args_hash,
            result_hash=r.result_hash,
            ok=r.ok,
            error=r.error,
            approved_action_id=r.approved_action_id,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.get("/runs/{run_id}/checkpoints")
async def read_checkpoints(
    run_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CheckpointRead]:
    """Checkpoint history, so "where did it stop" is answerable after the fact."""
    run = await _scoped_run(session, run_id, user)
    rows = await list_checkpoints(session, run.id)
    return [CheckpointRead(step=r.step, created_at=r.created_at.isoformat()) for r in rows]


@router.post("/runs/{run_id}/approve")
async def approve_action(
    run_id: uuid.UUID,
    payload: DecisionRequest,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Approve a pending state-changing action (T5.3).

    The pending action is loaded from the checkpoint, not from the request, so a
    caller cannot approve arguments the human was never shown.
    """
    run = await _scoped_run(session, run_id, user)
    state = await load_state(session, run.id)
    if state is None:
        raise HTTPException(status_code=409, detail="run has no checkpoint to resume")
    if state.pending is None:
        raise HTTPException(status_code=409, detail="no action is awaiting approval")
    if state.pending.tool not in _state_changing_tools():
        raise HTTPException(status_code=409, detail="pending action is not state-changing")

    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="agent.action.approve",
        target=str(state.pending.action_id),
        payload={
            "run_id": str(run.id),
            "tool": state.pending.tool,
            "args_hash": state.pending.args_hash(),
        },
    )
    await session.commit()

    # The action is applied here; the run then *continues* in the SSE stream.
    # Running it inline meant this POST blocked for the length of a whole model run,
    # so the operator watched "Applying…" indefinitely with no feedback and no
    # stream to watch. Same fix as `POST /runs`.
    # Unlike `start_run`, this one *does* need a model: applying the action goes
    # through the tool runner, which embeds a memory note about what was approved.
    graph = AgentGraph(session=session, model=_model(settings), settings=settings, run=run)
    try:
        await graph.approve_only(state, action_id=payload.action_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_payload(run, state)


@router.post("/runs/{run_id}/reject")
async def reject_action(
    run_id: uuid.UUID,
    payload: DecisionRequest,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Reject a pending action. Recorded as a decision, not silently dropped."""
    run = await _scoped_run(session, run_id, user)
    state = await load_state(session, run.id)
    if state is None:
        raise HTTPException(status_code=409, detail="run has no checkpoint to resume")
    if state.pending is None:
        raise HTTPException(status_code=409, detail="no action is awaiting approval")

    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="agent.action.reject",
        target=str(state.pending.action_id),
        payload={
            "run_id": str(run.id),
            "tool": state.pending.tool,
            "args_hash": state.pending.args_hash(),
            "reason": payload.reason,
        },
    )
    await session.commit()

    graph = AgentGraph(session=session, model=_model(settings), settings=settings, run=run)
    try:
        await graph.reject_only(state, action_id=payload.action_id, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_payload(run, state)


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Resume a run from its last checkpoint (T5.7)."""
    run = await _scoped_run(session, run_id, user)
    state = await load_state(session, run.id)
    if state is None:
        raise HTTPException(status_code=409, detail="run has no checkpoint to resume")
    if state.status in (RunStatus.COMPLETED, RunStatus.DECLINED):
        return _run_payload(run, state)

    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="agent.run.resume",
        target=str(run.id),
        payload={"from_step": state.step.value},
    )
    await session.commit()

    graph = AgentGraph(session=session, model=_model(settings), settings=settings, run=run)
    outcome = await graph.resume(state)
    return _run_payload(outcome.run, outcome.state)


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    resume: Annotated[bool, Query(description="Resume from the last checkpoint")] = True,
) -> StreamingResponse:
    """SSE progress stream for a run (P5.B10).

    Emits one event per step transition plus a terminal event carrying the
    report or the refusal. Uses SSE rather than WebSocket: traffic is
    one-directional once started, SSE survives proxies, and it needs no
    dependency.
    """
    run = await _scoped_run(session, run_id, user)
    state = await load_state(session, run.id)

    async def _events() -> AsyncIterator[str]:
        async def emit(event: str, data: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

        yield await emit("start", _run_payload(run, state))

        if state is None:
            yield await emit("error", {"error": "run has no checkpoint; nothing to stream"})
            return
        if state.status in (RunStatus.COMPLETED, RunStatus.DECLINED):
            yield await emit("done", _run_payload(run, state))
            return

        graph = AgentGraph(session=session, model=_model(settings), settings=settings, run=run)
        seen_steps: list[str] = [state.step.value]
        yield await emit("step", {"step": state.step.value})

        try:
            if resume:
                outcome = await graph.resume(state)
            else:
                outcome = await graph.run_until_pause(state)
        except Exception as exc:  # noqa: BLE001 - report, do not drop the stream
            yield await emit("error", {"error": f"{type(exc).__name__}: {exc}"})
            return

        final_state = outcome.state
        if final_state.step.value not in seen_steps:
            yield await emit("step", {"step": final_state.step.value})

        if final_state.pending is not None:
            yield await emit(
                "awaiting_approval",
                {
                    "action_id": final_state.pending.action_id,
                    "tool": final_state.pending.tool,
                    "args": final_state.pending.args,
                    "reason": final_state.pending.reason,
                },
            )
            yield await emit("done", _run_payload(outcome.run, final_state))
            return

        if final_state.step is Step.INSUFFICIENT_EVIDENCE:
            yield await emit("declined", {"reason": final_state.refusal})
        yield await emit("done", _run_payload(outcome.run, final_state))

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _state_changing_tools() -> set[str]:
    return {t["name"] for t in tool_catalog() if t.get("state_changing")}


__all__ = ["router", "Decision", "RunRead", "latest_checkpoint", "update_run"]

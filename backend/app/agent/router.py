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
    list_tool_calls,
    update_run,
)
from app.agent.security import InjectionDetected, validate_operator_instruction
from app.agent.tools import tool_catalog
from app.agent.types import AgentState, Decision, RunStatus, Step
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


def _run_payload(run: Any, state: AgentState | None) -> dict[str, Any]:
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
    }


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

    model = _model(settings)
    thread_id = payload.thread_id or f"case-{case.id}"
    run = await create_run(
        session,
        case_id=case.id,
        thread_id=thread_id,
        objective=objective,
        actor_id=uuid.UUID(user.id),
        model=model.name,
    )
    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="agent.run.start",
        target=str(run.id),
        payload={"case_id": str(case.id), "model": model.name, "thread_id": thread_id},
    )
    await session.commit()

    state = AgentState(
        run_id=str(run.id),
        case_id=str(case.id),
        thread_id=thread_id,
        objective=objective,
    )
    graph = AgentGraph(
        session=session,
        model=model,
        settings=settings,
        run=run,
        case_number=case.number,
        case_title=case.title,
    )
    outcome = await graph.start(state)
    return _run_payload(outcome.run, outcome.state)


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

    graph = AgentGraph(session=session, model=_model(settings), settings=settings, run=run)
    try:
        outcome = await graph.approve(state, action_id=payload.action_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_payload(outcome.run, outcome.state)


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
        outcome = await graph.reject(state, action_id=payload.action_id, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_payload(outcome.run, outcome.state)


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

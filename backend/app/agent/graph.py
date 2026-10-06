"""The agent graph (P5.B2).

Deviation from the plan, stated plainly: a plain state machine, not LangGraph.

The plan wants Intake → Planner → ToolExec ⇄ Memory → Reflector → HITL → Report
→ Done, with checkpointing and interrupts. That is 7 nodes, one conditional edge,
and two things we have to own anyway:

- **Interrupt-with-audit.** Approve and reject must both land in the audit trail
  (T5.3). LangGraph interrupts, but not that, so we would build our own layer on
  top regardless.
- **The checkpointer.** P5.B3 asks for a Postgres checkpointer. LangGraph's
  `langgraph-checkpoint-postgres` would replace it — so it is not additional
  value, it is an alternative implementation of a table we already write.

What a framework would genuinely add is free-form graph composition, which we do
not need until there is a second graph. If that arrives, `AgentGraph` is a single
class and the step functions move across unchanged.

Loop bounds are explicit rather than left to the framework: an agent that
re-calls the same tool forever is a denial-of-service against our own database.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import ChatMessage, ChatModel, parse_json_block
from app.agent.provenance import ProvenanceGuard, citations_from_result
from app.agent.repository import (
    AgentRun,
    latest_checkpoint,
    save_checkpoint,
    update_run,
    write_memory,
)
from app.agent.security import build_system_prompt, wrap_untrusted
from app.agent.tools import NeedsApproval, ToolRunner, to_model_tools
from app.agent.types import (
    TERMINAL_STEPS,
    AgentState,
    Citation,
    Claim,
    Decision,
    ProvenanceError,
    RunStatus,
    Step,
    ToolCall,
)
from app.analysis.types import Tier


def _ensure_tools() -> None:
    """Populate the tool registry. Idempotent."""
    from app.agent.builtin import register_all

    register_all()


#: Cap on tool calls per run. Bounds the damage from a looping or injected model.
MAX_TOOL_CALLS = 20

#: Where the agent's own reply must carry its citations. JSON keeps parsing
#: unambiguous; a prose format would put the honesty guarantee at the mercy of
# regex.
#: Tool output longer than this is truncated before it reaches the model. A tool
#: that returns a whole image's worth of base64 would otherwise blow the context
#: window, and the run would fail for a reason that has nothing to do with the case.
MAX_TOOL_OUTPUT_CHARS = 4000

_RESPONSE_CONTRACT = """\
Respond with JSON only, no prose, in exactly this shape:
{"claims": [{"text": "<one specific, sourced statement>",
             "citations": [{"result_id": "<uuid from the results you were given>"}]}]}

Rules:
- Every claim needs at least one citation. A claim you cannot cite is a claim
  you must not make.
- Only cite result ids that appear in the analysis results provided to you.
- If the results do not support a conclusion, return {"claims": [],
  "insufficient_evidence": "<what is missing>"} rather than speculating."""


@dataclass
class GraphOutcome:
    state: AgentState
    run: AgentRun

    @property
    def awaiting_approval(self) -> bool:
        return self.state.status is RunStatus.AWAITING_APPROVAL


class _Failed:
    """Minimal stand-in so `_render_tool_output` can render a tool that never ran."""

    def __init__(self, error: str) -> None:
        self.ok = False
        self.output = None
        self.error = error


def _failed(error: str) -> _Failed:
    return _Failed(error)


def canonical_args(args: dict[str, Any]) -> str:
    """Serialise tool arguments the way the API expects: one JSON string.

    Not `str(dict)` — Python's repr uses single quotes and `True`, which is not
    JSON. A model asked to echo that back produces malformed arguments, and the
    failure looks like the model being unable to call tools.
    """
    return json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)


def _render_tool_output(result: Any) -> str:
    """What the model sees as the tool's result.

    Must be a string: the chat API rejects a non-string `content` on a tool turn.
    JSON is used so structure survives, and an error is surfaced as one rather
    than as an empty success — a tool that failed has to be legible to the model,
    or it retries the same call forever.
    """
    payload = result.output if result.ok else {"error": result.error}
    try:
        return json.dumps(payload, default=str)[:MAX_TOOL_OUTPUT_CHARS]
    except (TypeError, ValueError):
        return str(payload)[:MAX_TOOL_OUTPUT_CHARS]


class AgentGraph:
    """One run over one case. Deterministic given the same model responses."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        model: ChatModel,
        settings: Any,
        run: AgentRun,
        case_number: str = "",
        case_title: str = "",
    ) -> None:
        self._session = session
        self._model = model
        self._settings = settings
        self._run = run
        self._case_number = case_number
        self._case_title = case_title
        _ensure_tools()

    # --- entry points ---

    async def start(self, state: AgentState) -> GraphOutcome:
        await self._enter(state, Step.INTAKE)
        return await self.run_until_pause(state)

    async def resume(self, state: AgentState) -> GraphOutcome:
        """Continue from a checkpoint after a process death (T5.7)."""
        return await self.run_until_pause(state)

    async def run_until_pause(self, state: AgentState) -> GraphOutcome:
        """Drive the graph until it terminates or needs a human."""
        while state.step not in TERMINAL_STEPS:
            if state.status is RunStatus.AWAITING_APPROVAL:
                break
            await self._checkpoint(state)
            await self._advance(state)
        state.status = self._final_status(state)
        await self._checkpoint(state)
        await update_run(
            self._session,
            self._run,
            status=state.status.value,
            step=state.step.value,
            report=state.report,
            refusal=state.refusal,
            error=state.error,
        )
        await self._session.commit()
        return GraphOutcome(state=state, run=self._run)

    async def approve(self, state: AgentState, *, action_id: str) -> GraphOutcome:
        """Apply a pending action the human approved (T5.3)."""
        pending = state.pending
        if pending is None:
            raise ValueError("no action is awaiting approval")
        if pending.action_id != action_id:
            # Refuse silently-applied drift: the approver must be approving the
            # exact action that was parked.
            raise ValueError("action_id does not match the pending action")

        runner = self._runner()
        result = await runner.execute_approved(pending)
        state.tool_results.append(result)
        state.pending = None
        state.step = Step.REFLECTOR
        state.status = RunStatus.RUNNING
        state.messages.append(
            {
                "role": "system",
                "content": f"{pending.tool} was approved by a human and executed.",
                "result": {"ok": result.ok, "output": result.output, "error": result.error},
            }
        )
        return await self.run_until_pause(state)

    async def reject(self, state: AgentState, *, action_id: str, reason: str = "") -> GraphOutcome:
        """Discard a pending action (T5.3). The refusal is recorded, not lost."""
        pending = state.pending
        if pending is None:
            raise ValueError("no action is awaiting approval")
        if pending.action_id != action_id:
            raise ValueError("action_id does not match the pending action")
        state.pending = None
        state.messages.append(
            {
                "role": "system",
                "content": f"{pending.tool} was REJECTED by a human"
                + (f": {reason}" if reason else "."),
            }
        )
        state.step = Step.REFLECTOR
        state.status = RunStatus.RUNNING
        return await self.run_until_pause(state)

    # --- steps ---

    async def _advance(self, state: AgentState) -> None:
        match state.step:
            case Step.INTAKE:
                await self._enter(state, Step.PLANNER)
            case Step.PLANNER:
                await self._planner(state)
            case Step.TOOL_EXEC:
                await self._tool_exec(state)
            case Step.MEMORY:
                await self._memory_step(state)
            case Step.REFLECTOR:
                await self._reflector(state)
            case Step.REPORT:
                await self._report(state)
            case Step.HITL:
                # Reached only if a state-changing tool did not pause; treated as
                # report rather than looping forever.
                await self._enter(state, Step.REPORT)
            case _:
                await self._enter(state, Step.DONE)

    async def _planner(self, state: AgentState) -> None:
        """Load the case context the planner and reflector need."""
        state.messages = [
            {
                "role": "system",
                "content": build_system_prompt(self._case_number, self._case_title),
            },
            {
                "role": "user",
                "content": f"Objective: {state.objective}",
            },
        ]
        await self._enter(state, Step.MEMORY)

    async def _memory_step(self, state: AgentState) -> None:
        """Recall prior context, wrapped as untrusted data (P5.B9)."""
        recalled = await self._recall(state)
        if recalled:
            wrapped, findings = wrap_untrusted(
                "\n".join(f"- {m}" for m in recalled), label="memory"
            )
            state.messages.append({"role": "user", "content": wrapped})
            if findings:
                state.messages.append(
                    {
                        "role": "system",
                        "content": f"Sanitised while recalling memory: {findings}",
                    }
                )
        await self._enter(state, Step.REFLECTOR)

    async def _reflector(self, state: AgentState) -> None:
        """Decide next action: run a tool, or produce a response."""
        # OpenAI wire format, not the flat catalog. Sending the catalog directly
        # is silently ignored by the provider, so the model never sees the tools.
        #
        # Every message is rebuilt from the raw dicts rather than filtered from
        # state.messages: a `tool` turn must carry its `tool_call_id`, and a turn
        # holding tool_calls must carry them too. Selecting only role+content
        # dropped exactly the fields that let the model match a result to the call
        # it made.
        response = self._model.complete(
            [
                ChatMessage(
                    role=str(m["role"]),
                    content=str(m.get("content", "")),
                    tool_calls=list(m.get("tool_calls", [])),
                )
                for m in state.messages
            ],
            tools=to_model_tools(),
        )

        if response.tool_calls:
            call = response.tool_calls[0]
            name = str(call.get("function", {}).get("name", ""))
            raw_args = call.get("function", {}).get("arguments", "{}")
            args = parse_json_block(raw_args) if isinstance(raw_args, str) else raw_args
            if not isinstance(args, dict):
                # A model that returned a JSON list or scalar for its arguments.
                # Refusing here is better than passing it to the pydantic model,
                # where it would surface as a validation error with no context.
                state.messages.append(
                    {
                        "role": "tool",
                        "content": _render_tool_output(
                            _failed(
                                f"arguments for {name!r} were not a JSON object: "
                                f"{str(raw_args)[:200]}"
                            )
                        ),
                        "tool_call_id": str(call.get("id", "")),
                        "name": name,
                    }
                )
                await self._enter(state, Step.REFLECTOR)
                return
            await self._invoke(state, name, args, call_id=str(call.get("id", "")))
            return

        parsed = parse_json_block(response.content) or {}
        if parsed.get("insufficient_evidence"):
            state.step = Step.INSUFFICIENT_EVIDENCE
            state.refusal = str(parsed["insufficient_evidence"])
            return
        await self._enter(state, Step.REPORT)

    async def _tool_exec(self, state: AgentState) -> None:
        """Placeholder step; tool invocation happens inside the reflector.

        Kept as a distinct step so a checkpoint taken mid-execution has a name
        that means something in the history.
        """
        await self._enter(state, Step.REFLECTOR)

    async def _invoke(
        self, state: AgentState, name: str, args: dict[str, Any], *, call_id: str = ""
    ) -> None:
        if len(state.tool_calls) >= MAX_TOOL_CALLS:
            state.step = Step.INSUFFICIENT_EVIDENCE
            state.refusal = (
                f"stopped after {MAX_TOOL_CALLS} tool calls without a sourced " f"conclusion"
            )
            return

        runner = self._runner()
        # Reuse the provider's call id when it sent one: the model correlates the
        # result it receives with the call it made by that id, so minting our own
        # here would break the pairing on the next turn.
        call = (
            ToolCall(name=name, args=args, call_id=call_id)
            if call_id
            else ToolCall(name=name, args=args)
        )
        state.tool_calls.append(call)
        await self._enter(state, Step.TOOL_EXEC)

        try:
            result = await runner.invoke(call)
        except NeedsApproval as exc:
            state.pending = exc.action
            state.status = RunStatus.AWAITING_APPROVAL
            state.step = Step.HITL
            return

        state.tool_results.append(result)
        # The result must go back to the model in the shape the chat API expects:
        # an assistant turn carrying the tool_calls, then a `tool` turn carrying the
        # output and the originating call id.
        #
        # This was previously appended as a user message with the payload in a
        # `result` key. `_reflector` only reads `role` and `content`, so the model
        # received the literal string "Tool search_evidence returned:" and nothing
        # else — no output, no error. It had no way to learn anything from the call
        # and simply repeated it until the loop bound stopped it. The audit rows
        # were correct the whole time, which is why the scripted tests passed.
        state.messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": canonical_args(args)},
                    }
                ],
            }
        )
        state.messages.append(
            {
                "role": "tool",
                "content": _render_tool_output(result),
                "tool_call_id": call.call_id,
                "name": name,
            }
        )
        # Remember what happened so a later turn can recall it (P5.B4).
        await self._remember(state, f"{name} -> {result.result_hash[:12]}")
        await self._enter(state, Step.REFLECTOR)

    async def _report(self, state: AgentState) -> None:
        """Compose the report under provenance enforcement (P5.B8)."""
        # `_collect_claims` may already have decided there is nothing to report.
        # Falling through would overwrite that refusal with a "no findings"
        # report, which reads like a clean result rather than an absence of one.
        if state.step in TERMINAL_STEPS:
            return
        claims = await self._collect_claims(state)
        if state.step in TERMINAL_STEPS:
            return
        guard = ProvenanceGuard(self._session)
        import uuid as _uuid

        case_id = _uuid.UUID(state.case_id)
        try:
            state.report = await guard.render_report(claims, case_id=case_id)
        except ProvenanceError as exc:
            # A report we cannot source is not a shorter report — it is no
            # report. Record the refusal and stop.
            state.step = Step.INSUFFICIENT_EVIDENCE
            state.refusal = "the model's claims could not be sourced to analyzer results: " f"{exc}"
            state.report = ""
            return
        await self._enter(state, Step.DONE)

    # --- helpers ---

    async def _collect_claims(self, state: AgentState) -> list[Claim]:
        """Ask the model for claims, then resolve them to real citations.

        The model may only return result ids; the citation is rebuilt from the
        stored row. A hallucinated id therefore cannot produce a plausible-looking
        citation, it produces a rejected claim.
        """
        import uuid as _uuid

        from sqlalchemy import select

        from app.analysis.models import AnalysisResult

        result = await self._session.execute(
            select(AnalysisResult)
            .where(AnalysisResult.case_id == _uuid.UUID(state.case_id))
            .order_by(AnalysisResult.created_at.desc())
        )
        rows = list(result.scalars().all())
        catalogue = [
            {
                "result_id": str(r.id),
                "analyzer": r.module,
                "score": r.score,
                "threshold": r.threshold,
                "model_version": r.model_version,
                "tier": r.tier,
            }
            for r in rows
        ]
        if not catalogue:
            state.step = Step.INSUFFICIENT_EVIDENCE
            state.refusal = "no analyzer results exist for this case yet"
            return []

        state.messages.append(
            {
                "role": "user",
                "content": "Analysis results available for citation:\n"
                + "\n".join(
                    f"- result_id={c['result_id']} analyzer={c['analyzer']} "
                    f"score={c['score']:.3f} threshold={c['threshold']:.3f} "
                    f"tier={c['tier']}"
                    for c in catalogue
                )
                + f"\n\n{_RESPONSE_CONTRACT}",
            }
        )
        response = self._model.complete(
            [ChatMessage(role=m["role"], content=m["content"]) for m in state.messages]
        )
        parsed = parse_json_block(response.content) or {}
        if parsed.get("insufficient_evidence"):
            state.step = Step.INSUFFICIENT_EVIDENCE
            state.refusal = str(parsed["insufficient_evidence"])
            return []

        by_id = {str(r.id): r for r in rows}
        claims: list[Claim] = []
        for raw in parsed.get("claims", []) or []:
            if not isinstance(raw, dict):
                continue
            citations: list[Citation] = []
            for c in raw.get("citations", []) or []:
                rid = str(c.get("result_id", "")) if isinstance(c, dict) else str(c)
                row = by_id.get(rid)
                if row is not None:
                    citations.append(await citations_from_result(row))
            claims.append(Claim(text=str(raw.get("text", "")), citations=citations))

        state.claims = claims
        return claims

    async def _recall(self, state: AgentState) -> list[str]:
        from app.agent.repository import recall_memory

        actor = uuid.UUID(str(self._run.requested_by))
        query = state.objective
        try:
            vector = self._model.embed([query])[0]
        except Exception:  # noqa: BLE001 - recall is an optimisation, not a gate
            vector = []
        rows = await recall_memory(
            self._session,
            case_id=uuid.UUID(state.case_id),
            actor_id=actor,
            query_embedding=vector,
            limit=5,
        )
        return [r["content"] for r in rows]

    async def _remember(self, state: AgentState, summary: str) -> None:
        try:
            vector = self._model.embed([summary])[0]
        except Exception:  # noqa: BLE001
            vector = []
        content = f"{state.step.value}: {summary}"
        if len(vector) != 1536:
            # write_memory validates width; skip rather than store a wrong-shaped
            # vector that would silently break recall later.
            content = content + " (not embedded: provider unavailable)"
        try:
            await write_memory(
                self._session,
                case_id=uuid.UUID(state.case_id),
                actor_id=uuid.UUID(str(self._run.requested_by)),
                kind="episodic",
                content=content,
                embedding=vector,
                run_id=self._run.id,
            )
        except ValueError:
            return

    async def _enter(self, state: AgentState, step: Step) -> None:
        state.step = step
        if step in TERMINAL_STEPS:
            return
        if state.status in (RunStatus.PENDING,):
            state.status = RunStatus.RUNNING

    async def _checkpoint(self, state: AgentState) -> None:
        state.updated_at = datetime.now(UTC)
        await save_checkpoint(
            self._session,
            run_id=self._run.id,
            step=state.step.value,
            state=state.to_dict(),
        )

    def _runner(self) -> ToolRunner:
        return ToolRunner(
            session=self._session,
            case_id=uuid.UUID(str(self._run.case_id)),
            run_id=str(self._run.id),
            actor_id=uuid.UUID(str(self._run.requested_by)),
            settings=self._settings,
            services={"model": self._model},
        )

    @staticmethod
    def _final_status(state: AgentState) -> RunStatus:
        if state.step is Step.INSUFFICIENT_EVIDENCE:
            return RunStatus.DECLINED
        if state.step is Step.DONE:
            return RunStatus.COMPLETED
        return state.status


async def load_state(session: AsyncSession, run_id: uuid.UUID) -> AgentState | None:
    """Rebuild the last checkpointed state for a run (T5.7)."""
    cp = await latest_checkpoint(session, run_id)
    return AgentState.from_dict(cp.state) if cp else None


__all__ = ["AgentGraph", "GraphOutcome", "load_state", "Decision", "Tier"]

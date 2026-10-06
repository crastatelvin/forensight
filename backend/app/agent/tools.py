"""Tool registry (P5.B6) with mandatory audit and HITL gating (P5.B7).

Three properties every tool call must have, enforced here rather than in each
tool:

- **Typed input.** Arguments validate against a Pydantic model before the tool
  runs. A tool cannot receive an argument it never declared.
- **Audited.** Every call is written to `agent_tool_calls` with its argument hash
  and result hash. A call that fails validation is still recorded — a rejected
  call is evidence of what the agent tried to do.
- **Case-scoped.** The case id comes from the run, never from the model's
  arguments, so no tool call can reach outside the case it was started for. This
  is what makes prompt injection survivable: even a fully jailbroken model is
  bounded by this, not by the system prompt alone.

Tools that change state declare `state_changing=True` and are not executed
directly. They raise `NeedsApproval` carrying a frozen copy of the arguments,
so the human approves exactly what was shown and nothing is re-derived from
later model output.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from app.agent.types import PendingAction, ToolCall, ToolResult


class NeedsApproval(Exception):
    """Raised by a state-changing tool to request human authorisation.

    Carries the action so the graph can park it. The arguments are copied here,
    at the moment of the request, so approval cannot be laundered into approving
    something else.
    """

    def __init__(self, tool: str, args: dict[str, Any], reason: str) -> None:
        self.action = PendingAction(
            action_id=str(uuid.uuid4()), tool=tool, args=dict(args), reason=reason
        )
        super().__init__(reason)


class ToolError(Exception):
    """A tool ran and failed. Recorded with the call, not raised to the model."""


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    args_model: type[BaseModel]
    state_changing: bool

    async def __call__(self, ctx: ToolContext, args: BaseModel) -> Any: ...


@dataclass
class ToolContext:
    """Everything a tool is allowed to reach.

    Deliberately narrow: a tool gets a DB session, the case it is scoped to, the
    run, and settings. No model client, no raw request, no credentials. The
    narrower this is, the less a compromised agent can leverage.
    """

    session: Any
    case_id: uuid.UUID
    run_id: str
    actor_id: uuid.UUID
    settings: Any
    services: dict[str, Any] = field(default_factory=dict)


_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in _TOOLS:
        raise ValueError(f"tool {tool.name!r} is already registered")
    _TOOLS[tool.name] = tool
    return tool


def get(name: str) -> Tool:
    try:
        return _TOOLS[name]
    except KeyError:
        known = ", ".join(sorted(_TOOLS)) or "none"
        raise ToolError(f"unknown tool {name!r}; registered: {known}") from None


def tool_names() -> list[str]:
    return sorted(_TOOLS)


def registry() -> dict[str, Tool]:
    return dict(_TOOLS)


def reset() -> None:
    _TOOLS.clear()


class ToolRunner:
    """Validates, gates, executes, and records. The only path to a tool body."""

    def __init__(
        self,
        *,
        session: Any,
        case_id: uuid.UUID,
        run_id: str,
        actor_id: uuid.UUID,
        settings: Any,
        services: dict[str, Any] | None = None,
    ) -> None:
        self._session = session
        self._ctx = ToolContext(
            session=session,
            case_id=case_id,
            run_id=run_id,
            actor_id=actor_id,
            settings=settings,
            services=services or {},
        )
        self._case_id = case_id
        self._run_id = run_id

    async def invoke(self, call: ToolCall) -> ToolResult:
        """Run one tool call. Never raises for tool failure.

        A failing tool must be recorded and reported back to the model, not
        crash the run: the agent's ability to recover depends on seeing what
        failed.
        """
        result = ToolResult(call_id=call.call_id, name=call.name, ok=False, output=None)
        try:
            tool = get(call.name)
        except ToolError as exc:
            result.error = str(exc)
            result.compute_hash()
            await self._record(call, result)
            return result

        # Typed input, before the tool body can see anything.
        try:
            args = tool.args_model(**call.args)
        except ValidationError as exc:
            result.error = f"invalid arguments: {exc.errors()[0]['msg']}"
            result.compute_hash()
            await self._record(call, result)
            return result

        if tool.state_changing:
            # Park for a human instead of acting. Arguments are frozen here.
            raise NeedsApproval(call.name, call.args, f"{call.name} changes case state")

        try:
            result.output = await tool(self._ctx, args)
            result.ok = True
        except NeedsApproval:
            raise
        except Exception as exc:  # noqa: BLE001 - a tool failure is data, not a crash
            result.error = f"{type(exc).__name__}: {exc}"
        result.compute_hash()
        await self._record(call, result)
        return result

    async def execute_approved(self, action: PendingAction) -> ToolResult:
        """Run a previously approved state-changing call.

        Re-validates the arguments and re-checks the action hash, so an approval
        cannot be replayed against different arguments or after the case moved
        on.
        """
        call = ToolCall(name=action.tool, args=dict(action.args))
        tool = get(action.tool)
        args = tool.args_model(**action.args)
        result = ToolResult(call_id=call.call_id, name=call.name, ok=False, output=None)
        try:
            result.output = await tool(self._ctx, args)
            result.ok = True
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
        result.compute_hash()
        await self._record(call, result, approved_action_id=action.action_id)
        return result

    async def _record(
        self, call: ToolCall, result: ToolResult, *, approved_action_id: str = ""
    ) -> None:
        from app.agent.repository import record_tool_call

        await record_tool_call(
            self._session,
            run_id=uuid.UUID(self._run_id),
            call=call,
            result=result,
            case_id=self._case_id,
            approved_action_id=approved_action_id,
        )


def define_tool(
    *,
    name: str,
    description: str,
    args_model: type[BaseModel],
    state_changing: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator turning an async function into a registered Tool.

    Tools are async because every data source they touch is: Postgres via
    SQLAlchemy async, object storage, the model client. Making the signature sync
    to hide that would force a bridge per tool and tempt someone into blocking
    the event loop.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        # Attributes are set on the function rather than wrapped in a class: the
        # registry calls the registered object directly, so wrapping it would
        # make `tool(ctx, args)` construct an instance instead of running it.
        fn.name = name  # type: ignore[attr-defined]
        fn.description = description  # type: ignore[attr-defined]
        fn.args_model = args_model  # type: ignore[attr-defined]
        fn.state_changing = state_changing  # type: ignore[attr-defined]
        return fn

    return decorate


def tool_catalog() -> list[dict[str, Any]]:
    """Registry view of the tools: name, description, schema, approval flag.

    This is what `/agent/tools` serves to the UI and what tests assert against.
    It is *not* what goes on the wire to a model — see `to_model_tools`.
    """
    out: list[dict[str, Any]] = []
    for name in sorted(_TOOLS):
        tool = _TOOLS[name]
        out.append(
            {
                "name": tool.name,
                "description": tool.description,
                "state_changing": tool.state_changing,
                "parameters": tool.args_model.model_json_schema(),
            }
        )
    return out


def to_model_tools(catalog: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Convert the catalog into the OpenAI `tools` wire format.

    Each entry must be `{"type": "function", "function": {...}}`. Sending the
    catalog's flat entries instead does **not** error: the endpoint accepts the
    request, the model is simply never told the tools exist, and it answers in
    prose. That failure is invisible in tests -- a scripted model returns
    whatever it was told to return regardless -- and it looks exactly like "the
    model is too small to use tools". `test_model_tool_payload_is_openai_shaped`
    pins the shape so it cannot regress.

    `state_changing` is deliberately dropped: it is an internal routing fact, not
    something a model should be reasoning about, and the tool body enforces it
    regardless of what the model says.
    """
    entries = tool_catalog() if catalog is None else catalog
    return [
        {
            "type": "function",
            "function": {
                "name": entry["name"],
                "description": entry["description"],
                "parameters": entry["parameters"],
            },
        }
        for entry in entries
    ]

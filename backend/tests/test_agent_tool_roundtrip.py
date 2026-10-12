"""The agent's conversation contract with the model (P6.E).

Every one of these exists because of a bug that green tests missed. The pattern
repeated three times, which is why the last test here is a structural guard rather
than another assertion:

1. `_invoke` parked a tool result under a `"result"` key on a *user* message.
   `_reflector` rebuilt messages from `role` and `content` only, so the model saw
   the literal string "Tool search_evidence returned:" and nothing else. It had no
   way to learn from the call, so it repeated the same call 20 times against a live
   provider and then declined.
2. Tools were sent to the provider as a flat catalog rather than the OpenAI
   `{"type": "function", "function": {...}}` shape. Every OpenAI-compatible
   endpoint accepts that without error and then ignores it, so the model was never
   told the tools existed.
3. `approve()` parked the approved action's outcome under the same unread
   `"result"` key, so after a human approved something the model still could not
   see that it had happened.

A scripted model cannot catch any of these: it replays responses regardless of
what it was sent. Only a live provider, or an assertion about the exact bytes on
the wire, reveals them. `tests/test_agent_live.py` covers the live half.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.agent.builtin import RunAnalysisArgs, run_analysis
from app.agent.builtin import register_all as register_agent_tools
from app.agent.graph import AgentGraph
from app.agent.llm import ChatMessage, ChatResponse, OpenAICompatModel, ScriptedModel
from app.agent.tools import NeedsApproval, ToolContext, ToolError, to_model_tools, tool_catalog
from app.agent.types import ToolCall, ToolResult
from httpx import AsyncClient

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdacd\xf8"
    b"\xcfP\x0f\x00\x03\x86\x01\x80Z4}k\x00\x00\x00IEND\xaeB`\x82"
)


def _settings() -> Any:
    from app.config import Settings

    return Settings(secret_key="s" * 40, minio_endpoint="")


def _register_everything() -> None:
    """Both registries: `run_analysis` resolves its module in the analysis one.

    Registering only the agent tools was itself a bug -- every `run_analysis` call
    raised `KeyError: unknown analysis module`.
    """
    from app.analysis.registry import register_all as register_analyzers

    register_agent_tools()
    register_analyzers()


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    r = await client.post("/auth/login", json=creds)
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _case_with_evidence(
    client: AsyncClient, headers: dict[str, str], fake_store: Any, number: str
) -> tuple[str, str]:
    cid = (
        await client.post("/cases", headers=headers, json={"number": number, "title": "T"})
    ).json()["id"]
    up = await client.post(
        f"/cases/{cid}/evidence",
        headers=headers,
        files={"file": ("p.png", _PNG, "application/octet-stream")},
    )
    assert up.status_code == 201, up.text
    return cid, up.json()["id"]


async def _analysis_ctx(
    sessionmaker_fixture: Any, cid: str, actor: uuid.UUID
) -> tuple[ToolContext, Any]:
    """A ToolContext scoped to one case, plus the holder so it can be closed."""
    from app.agent.repository import create_run, get_run

    async with sessionmaker_fixture() as setup:
        run = await create_run(
            setup,
            case_id=uuid.UUID(cid),
            thread_id=f"t-{uuid.uuid4().hex[:8]}",
            objective="analyse",
            actor_id=actor,
            model="scripted",
        )
        await setup.commit()
        run_id = run.id

    holder = sessionmaker_fixture()
    session = await holder.__aenter__()
    run_row = await get_run(session, run_id)
    assert run_row is not None
    ctx = ToolContext(
        session=session,
        case_id=uuid.UUID(cid),
        run_id=str(run_id),
        actor_id=actor,
        settings=_settings(),
    )
    return ctx, holder


# --- 1. A tool result must reach the model as a tool turn -------------------


def _drive(sessionmaker_fixture: Any, scripted: ScriptedModel) -> Any:
    raise NotImplementedError


async def _run_graph(sessionmaker_fixture: Any, scripted: ScriptedModel) -> Any:
    """Drive the graph to a pause and return its state."""
    from app.agent.repository import create_run
    from app.agent.types import AgentState
    from app.domain.models import Case

    actor = uuid.uuid4()
    async with sessionmaker_fixture() as session:
        case = Case(number=f"AG-{uuid.uuid4().hex[:6]}", title="T", owner_id=actor)
        session.add(case)
        await session.flush()
        run = await create_run(
            session,
            case_id=case.id,
            thread_id=f"t-{uuid.uuid4().hex[:6]}",
            objective="investigate",
            actor_id=actor,
            model="scripted",
        )
        await session.commit()
        run_id, case_id, number, title = run.id, case.id, case.number, case.title

    async with sessionmaker_fixture() as session:
        from app.agent.repository import get_run

        run_row = await get_run(session, run_id)
        assert run_row is not None
        graph = AgentGraph(
            session=session,
            model=scripted,
            settings=_settings(),
            run=run_row,
            case_number=number,
            case_title=title,
        )
        state = AgentState(
            run_id=str(run_id),
            case_id=str(case_id),
            thread_id=f"t-{uuid.uuid4().hex[:6]}",
            objective="investigate",
        )
        return await graph.start(state), graph, run_id, case_id, number, title


def _tool_call(name: str, args: str, call_id: str = "c1") -> ChatResponse:
    return ChatResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
        ],
    )


async def test_tool_result_reaches_the_model_as_a_tool_turn(
    sessionmaker_fixture,
) -> None:
    """The model must be able to read what its tool returned.

    This is bug 1. The audit rows were correct throughout, which is exactly why
    every other agent test passed.
    """
    scripted = ScriptedModel(
        [
            _tool_call("get_case", "{}"),
            ChatResponse(content=json.dumps({"insufficient_evidence": "done"})),
        ]
    )
    await _run_graph(sessionmaker_fixture, scripted)

    tool_turns = [m for turn in scripted.calls for m in turn if m.role == "tool"]
    assert tool_turns, "no tool-role turn was ever sent to the model"
    content = tool_turns[0].content
    assert content, "the tool turn had empty content"
    assert content != "Tool get_case returned:", "the result is still parked out of band"
    assert content.lstrip().startswith("{"), f"the model cannot read the result: {content[:200]!r}"


async def test_tool_turn_carries_the_originating_call_id(sessionmaker_fixture) -> None:
    """Without `tool_call_id` the model cannot match a result to the call."""
    scripted = ScriptedModel(
        [
            _tool_call("get_case", "{}", call_id="call_xyz"),
            ChatResponse(content=json.dumps({"insufficient_evidence": "done"})),
        ]
    )
    await _run_graph(sessionmaker_fixture, scripted)

    last = scripted.calls[-1]
    assistant = [m for m in last if m.role == "assistant" and m.tool_calls]
    assert assistant, "the assistant turn did not echo its tool_calls"
    assert assistant[0].tool_calls[0]["id"] == "call_xyz", (
        "the assistant turn dropped the provider's call id, so the model cannot "
        "correlate the result it is about to receive"
    )


async def test_failed_tool_is_legible_to_the_model(sessionmaker_fixture) -> None:
    """A failed tool must read as a failure.

    An empty or silently-dropped error makes the model retry the same call, which
    is how a live run burned its whole tool budget on one query.
    """
    scripted = ScriptedModel(
        [
            _tool_call("no_such_tool", "{}"),
            ChatResponse(content=json.dumps({"insufficient_evidence": "gave up"})),
        ]
    )
    await _run_graph(sessionmaker_fixture, scripted)

    tool_turns = [m for turn in scripted.calls for m in turn if m.role == "tool"]
    assert tool_turns
    assert (
        "no_such_tool" in tool_turns[0].content
    ), f"the model was not told the tool failed: {tool_turns[0].content[:200]!r}"


# --- 2. The tools must reach the provider in the shape it expects -----------


def test_model_tool_payload_is_openai_shaped() -> None:
    """Tools must be wrapped as `{"type": "function", "function": {...}}`.

    This is bug 2. A flat catalog is accepted by every OpenAI-compatible endpoint
    without error and the model is simply never told the tools exist, so it answers
    in prose. Indistinguishable from "the model is too small to use tools".
    """
    for entry in to_model_tools():
        assert entry["type"] == "function"
        assert set(entry) == {"type", "function"}, entry.keys()
        fn = entry["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object", "models need a JSON schema"


def test_to_model_tools_drops_the_internal_approval_flag() -> None:
    """`state_changing` is our routing fact; the tool body enforces it anyway."""
    entries = to_model_tools()
    assert not any("state_changing" in e["function"] for e in entries)
    assert any(t["state_changing"] for t in tool_catalog()), "the flag must still exist"


def test_catalogue_and_wire_formats_stay_in_step() -> None:
    assert {t["name"] for t in tool_catalog()} == {e["function"]["name"] for e in to_model_tools()}


class _FakeHttp:
    """Captures the request body instead of sending it."""

    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}

    def __call__(self) -> _FakeHttp:
        return self

    def __enter__(self) -> _FakeHttp:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, json: dict[str, Any]) -> Any:  # noqa: A002
        self.sent.update(json)
        return _FakeResp()


class _FakeResp:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"model": "m", "choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}


def _stub_model(client: _FakeHttp) -> OpenAICompatModel:
    # Built through the real constructor rather than `__new__` plus hand-assigned
    # attributes. The previous version listed the attributes it happened to need, so
    # adding one to `OpenAICompatModel` left this stub missing it and two tool-shape
    # tests failed with an AttributeError from `complete` — a hand-written duplicate of a
    # constructor, drifting from the one it copies. Only the HTTP client is replaced.
    model = OpenAICompatModel(
        name="stub",
        base_url="http://x/v1",
        model="m",
        embed_model="e",
        timeout=5,
    )
    model._client = client  # type: ignore[method-assign]
    return model


def test_llm_normalises_a_flat_catalog() -> None:
    """The client coerces a flat entry, so no caller can reintroduce bug 2."""
    http = _FakeHttp()
    _stub_model(http).complete(
        [ChatMessage(role="user", content="hi")],
        tools=[{"name": "t", "description": "d", "parameters": {"type": "object"}}],
    )
    assert http.sent["tools"] == [
        {
            "type": "function",
            "function": {"name": "t", "description": "d", "parameters": {"type": "object"}},
        }
    ]
    assert http.sent["tool_choice"] == "auto"


def test_llm_does_not_double_wrap_already_wrapped_tools() -> None:
    http = _FakeHttp()
    wrapped = [
        {
            "type": "function",
            "function": {"name": "t", "description": "d", "parameters": {"type": "object"}},
        }
    ]
    _stub_model(http).complete([ChatMessage(role="user", content="hi")], tools=wrapped)
    assert http.sent["tools"] == wrapped


# --- 3. After a human approves, the model must learn what happened -----------


async def test_approved_action_result_reaches_the_model(sessionmaker_fixture) -> None:
    """This is bug 3, on the approval path.

    The unread-`"result"`-key bug meant a run that parked a finding, got approved,
    and then continued could not tell that the write had happened.
    """
    scripted = ScriptedModel(
        [
            # Args must satisfy UpdateFindingArgs: title is required, body optional.
            # A payload that fails schema validation is recorded as a tool error and
            # the run never reaches the HITL gate, which is what made an earlier
            # version of this test fail with a misleading "must park for a human".
            _tool_call(
                "update_case_finding",
                json.dumps({"title": "Window was forced", "body": "seen"}),
                call_id="c-approve",
            ),
            ChatResponse(content=json.dumps({"insufficient_evidence": "stop"})),
        ]
    )
    first, graph, run_id, case_id, number, title = await _run_graph(sessionmaker_fixture, scripted)

    assert first.awaiting_approval, "a state-changing tool must park for a human"
    assert first.state.pending is not None
    assert NeedsApproval  # the gate is what produced the pending action

    turns_before = len(scripted.calls)
    async with sessionmaker_fixture() as session:
        from app.agent.repository import get_run
        from app.agent.types import AgentState

        run_row = await get_run(session, run_id)
        assert run_row is not None
        resumed_graph = AgentGraph(
            session=session,
            model=scripted,
            settings=_settings(),
            run=run_row,
            case_number=number,
            case_title=title,
        )
        resumed = AgentState.from_dict(first.state.to_dict())
        await resumed_graph.approve(resumed, action_id=first.state.pending.action_id)

    after = scripted.calls[turns_before - 1 :]
    tool_turns = [m for turn in after for m in turn if m.role == "tool"]
    assert tool_turns, "after approval the model was sent no tool turn"
    assert tool_turns[0].content, "the approval result was sent empty"
    assert (
        "finding_id" in tool_turns[0].content
    ), f"the approval result was not legible to the model: {tool_turns[0].content[:200]!r}"


# --- 4. run_analysis must return something the model can act on ------------


async def test_run_analysis_returns_a_citable_result_inline(
    client: AsyncClient, seed_users, fake_store, app, sessionmaker_fixture
) -> None:
    """No broker: the tool runs the job and hands back a citable score.

    This was the P6.E blocker. `run_analysis` used to return
    `"analysis is asynchronous; re-read results before citing"` -- an instruction
    the tool loop could not carry out, because the only tool that shows results is
    `search_evidence`, which the model must think to call, and which reads whatever
    exists *at the time it runs*. A result produced after that was invisible for the
    rest of the run, so the agent always declined for lack of results.

    Inline execution here is not a simulation: it is the same `execute_job` the
    Dramatiq worker calls, with the same pinned-model verification and the same
    refusal statuses. Only the transport differs.
    """
    _register_everything()
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "RT-1")

    ctx, holder = await _analysis_ctx(sessionmaker_fixture, cid, uuid.uuid4())
    try:
        result = await run_analysis(
            ctx, RunAnalysisArgs(module="byte-entropy", evidence_id=uuid.UUID(eid))
        )
    finally:
        await holder.__aexit__(None, None, None)

    assert result["result_available"] is True, result
    assert result["status"] == "done"
    payload = result["result"]
    assert payload is not None, "result_available was true but no payload came back"
    assert payload["analyzer"] == "byte-entropy"
    # A score is never returned without the bar it was judged against.
    assert payload["threshold"] is not None
    assert payload["model_version"]


async def test_run_analysis_reports_refusal_rather_than_a_score(
    client: AsyncClient, seed_users, fake_store, app, sessionmaker_fixture
) -> None:
    """When the analysis cannot run, the tool says so and invents nothing."""
    _register_everything()
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "RT-2")

    ctx, holder = await _analysis_ctx(sessionmaker_fixture, cid, uuid.uuid4())
    try:
        result = await run_analysis(
            ctx, RunAnalysisArgs(module="image-ela", evidence_id=uuid.UUID(eid))
        )
    finally:
        await holder.__aexit__(None, None, None)

    assert result["result_available"] is False, result
    assert result["status"] == "not_applicable"
    assert "result" not in result, "a refusal must not carry a score"
    assert "PNG" in result["reason"], result["reason"]


async def test_run_analysis_cannot_read_another_cases_evidence(
    client: AsyncClient, seed_users, fake_store, app, sessionmaker_fixture
) -> None:
    """Case scope comes from the run, so naming another case's evidence fails."""
    _register_everything()
    officer = await _auth(client, seed_users["officer"])
    case_a = (
        await client.post("/cases", headers=officer, json={"number": "RT-A", "title": "A"})
    ).json()["id"]
    case_b = (
        await client.post("/cases", headers=officer, json={"number": "RT-B", "title": "B"})
    ).json()["id"]
    up_b = await client.post(
        f"/cases/{case_b}/evidence",
        headers=officer,
        files={"file": ("b.png", _PNG, "application/octet-stream")},
    )
    evidence_in_b = up_b.json()["id"]

    ctx, holder = await _analysis_ctx(sessionmaker_fixture, case_a, uuid.uuid4())
    try:
        with pytest.raises(ToolError, match="another case"):
            await run_analysis(
                ctx,
                RunAnalysisArgs(module="byte-entropy", evidence_id=uuid.UUID(evidence_in_b)),
            )
    finally:
        await holder.__aexit__(None, None, None)


# --- The structural guard --------------------------------------------------


def test_no_message_parks_a_tool_result_out_of_band() -> None:
    """No message may carry a tool outcome anywhere but `content`.

    The scan is on the conversation, not on dict keys in general: a tool's return
    value may legitimately contain a key called `result` -- that is the payload
    handed to the model, not a side channel. What is forbidden is a message dict
    that holds an outcome in a key `_reflector` does not read.
    """
    import pathlib as _pathlib

    offenders: list[str] = []
    for path in sorted(_pathlib.Path("app/agent").rglob("*.py")):
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith(('"role": "assistant"', '"role": "tool"')):
                continue
            window = lines[index : index + 6]
            if any(other.strip().startswith('"result":') for other in window):
                offenders.append(f"{path}:{index + 1}: {stripped}")
    assert not offenders, "out-of-band tool results the model never receives:\n  " + "\n  ".join(
        offenders
    )


def test_tool_output_is_truncated_not_dumped() -> None:
    """A huge tool result must not blow the context window."""
    from app.agent.graph import MAX_TOOL_OUTPUT_CHARS

    big = {"blob": "x" * (MAX_TOOL_OUTPUT_CHARS * 3)}

    class _Ok:
        ok = True
        output = big
        error = ""

    from app.agent.graph import _render_tool_output

    assert len(_render_tool_output(_Ok())) <= MAX_TOOL_OUTPUT_CHARS


def test_canonical_args_is_json_not_python_repr() -> None:
    """Python's repr uses single quotes and `True`, which is not JSON."""
    from app.agent.graph import canonical_args

    rendered = canonical_args({"query": "it's here", "limit": 3, "flag": True})
    assert json.loads(rendered) == {"query": "it's here", "limit": 3, "flag": True}


# --- The audit row must be written, or refuse to write it (P6.E) -----------


async def test_record_tool_call_refuses_a_mis_typed_call(sessionmaker_fixture) -> None:
    """A type check here must survive `python -O`.

    `assert` is stripped from optimised bytecode. If `record_tool_call` used one
    to narrow its arguments, then under `-O` the check would vanish and a
    mis-typed call would fall through to an `AttributeError` — in a function whose
    entire job is writing a trustworthy audit row. Bandit flagged exactly this, and
    it was a real defect rather than a lint nit.
    """
    import uuid as _uuid

    from app.agent.repository import record_tool_call

    async with sessionmaker_fixture() as session:
        with pytest.raises(TypeError, match="ToolCall"):
            await record_tool_call(
                session,
                run_id=_uuid.uuid4(),
                call=object(),  # type: ignore[arg-type]
                result=ToolResult(call_id="c", name="t", ok=True, output=None),
                case_id=_uuid.uuid4(),
            )
        with pytest.raises(TypeError, match="ToolResult"):
            await record_tool_call(
                session,
                run_id=_uuid.uuid4(),
                call=ToolCall(name="t", args={}),
                result=object(),  # type: ignore[arg-type]
                case_id=_uuid.uuid4(),
            )


async def test_record_tool_call_computes_a_missing_hash(sessionmaker_fixture) -> None:
    """An unhashed result must be hashed before it is stored.

    This is the whole point of the audit row: a call recorded with an empty
    result_hash cannot be used to prove what happened.
    """
    import uuid as _uuid

    from app.agent.repository import record_tool_call
    from app.agent.types import ToolResult

    async with sessionmaker_fixture() as session:
        row = await record_tool_call(
            session,
            run_id=_uuid.uuid4(),
            call=ToolCall(name="get_case", args={}),
            result=ToolResult(call_id="c1", name="get_case", ok=True, output={"a": 1}),
            case_id=_uuid.uuid4(),
        )
    assert row.result_hash, "a tool call was recorded with no result hash"
    assert row.args_hash, "a tool call was recorded with no argument hash"


def test_a_generation_is_bounded() -> None:
    """No `max_tokens` in the payload means an unbounded generation.

    Measured on the constrained-host profile: `qwen2.5:3b` on CPU generates at about
    7 tokens/second, so an unbounded agent step is tens of seconds of CPU and can outlast
    the client's own 60 second timeout — which turns a slow answer into a `ModelError`
    and a failed run. The bound is a constructor concern, so it is asserted here rather
    than in the module docstring alone.
    """
    http = _FakeHttp()
    _stub_model(http).complete([ChatMessage(role="user", content="hi")])
    assert http.sent["max_tokens"] == 1024


def test_the_bound_comes_from_configuration() -> None:
    """`LLM_MAX_TOKENS` is the deployment's knob, and `resolve_model` threads it."""
    from app.agent.llm import resolve_model

    class _Settings:
        llm_provider = "ollama"
        llm_model = "qwen2.5"
        llm_base_url = ""
        llm_api_key = ""
        llm_embed_model = ""
        embed_dim = 1024
        llm_max_tokens = 256
        egress_allowed_hosts = ""

    assert resolve_model(_Settings()).max_tokens == 256  # type: ignore[attr-defined]


def test_the_timeout_comes_from_configuration() -> None:
    """`LLM_TIMEOUT` must reach the client, and default high enough to be usable.

    The timeout was hardcoded to 60 s, which is below what the constrained host this
    project targets can produce: 1024 tokens at the measured ~5 tokens/second is ~206 s.
    Every agent run on a local model therefore died with `ModelError: timed out`, and
    nothing caught it because the suite mocks the model.
    """
    from app.agent.llm import resolve_model
    from app.config import Settings

    class _Settings:
        llm_provider = "ollama"
        llm_model = "qwen2.5"
        llm_base_url = ""
        llm_api_key = ""
        llm_embed_model = ""
        embed_dim = 1024
        llm_max_tokens = 1024
        llm_timeout = 412.5
        egress_allowed_hosts = ""

    assert resolve_model(_Settings()).timeout == 412.5  # type: ignore[attr-defined]

    # And the shipped default must outlast the shipped token budget at the measured rate.
    default = Settings(_env_file=None)  # type: ignore[call-arg]
    assert default.llm_timeout >= 300.0, default.llm_timeout
    # 1024 tokens at ~5 tokens/s is ~206 s; anything under that truncates every step.
    assert default.llm_timeout >= default.llm_max_tokens / 5 * 1.2

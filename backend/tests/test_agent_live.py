"""Live end-to-end agent run against a real model (Phase 5 gap, T5.8).

Skipped unless FORENSIGHT_LLM_LIVE=1 and an OpenAI-compatible provider is
reachable. Everything else in the suite drives the graph through `ScriptedModel`,
which is right for testing provenance, scope and approval — those risks do not
need a provider. What a scripted model cannot test is whether a *real* model
actually emits tool calls, and that is exactly what this file exists for.

This test is the difference between "the tool path is proven" and "the tool path
works". The prior claim of a 3B model being unable to use tools turned out to be
a wire-format bug: tools were sent as a flat catalog, which every
OpenAI-compatible endpoint accepts and silently ignores.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from app.agent.graph import AgentGraph
from app.agent.llm import ChatMessage, resolve_model
from app.agent.repository import create_run, list_tool_calls
from app.config import Settings
from sqlalchemy.ext.asyncio import AsyncSession

# Live tests need a real schema: the graph writes checkpoints, tool-call rows and
# embeddings, and the SQLite in-memory engine used elsewhere does not carry the
# agent/analysis tables.
pytestmark = [
    pytest.mark.skipif(
        os.environ.get("FORENSIGHT_LLM_LIVE") != "1",
        reason="requires a live LLM (set FORENSIGHT_LLM_LIVE=1)",
    ),
]

_DSN = "postgresql+asyncpg://forensight:change-me-in-real-env@localhost:5432/forensight"


def _live_settings() -> Settings:
    """Provider settings, defaulting to a local Ollama if nothing is configured."""
    return Settings(
        secret_key="live-test-key-not-a-real-secret-0123456789",
        minio_endpoint=os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        minio_access_key=os.environ.get("MINIO_ACCESS_KEY", "forensight"),
        minio_secret_key=os.environ.get("MINIO_SECRET_KEY", "forensight"),
        llm_provider=os.environ.get("LLM_PROVIDER") or "ollama",
        llm_base_url=os.environ.get("LLM_BASE_URL") or "http://localhost:11434/v1",
        llm_model=os.environ.get("LLM_MODEL") or "qwen2.5:3b",
        llm_api_key=os.environ.get("LLM_API_KEY") or "",
        llm_embed_model=os.environ.get("LLM_EMBED_MODEL") or "bge-m3",
    )


@pytest_asyncio.fixture
async def pg() -> AsyncIterator[AsyncSession]:
    """A live Postgres session with the real schema applied."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(os.environ.get("DATABASE_URL", _DSN))
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture(scope="module")
def live_model() -> Any:
    settings = _live_settings()
    try:
        model = resolve_model(settings)
        model.complete([ChatMessage(role="user", content="ping")])
    except Exception as exc:  # noqa: BLE001 - provider down is a skip, not a failure
        pytest.skip(f"no live provider at {settings.llm_base_url}: {exc}")
    return model


# --- the gap itself ----------------------------------------------------------


def test_live_model_receives_openai_shaped_tools(live_model: Any) -> None:
    """The wire format, proven against a real endpoint.

    Sent flat, the endpoint returns 200 and the model answers in prose. Sent
    wrapped, it returns finish_reason=tool_calls. Nothing errors in between, which
    is why this took a live run to find.
    """
    from app.agent.builtin import register_all
    from app.agent.tools import to_model_tools

    register_all()
    tools = to_model_tools()
    assert tools and all(t["type"] == "function" for t in tools)

    response = live_model.complete(
        [
            ChatMessage(
                role="system",
                content=(
                    "You are a forensic agent. You must call the available tool to "
                    "begin investigating. Call exactly one tool."
                ),
            ),
            ChatMessage(role="user", content="Objective: begin investigating this case."),
        ],
        tools=tools,
    )
    assert response.tool_calls, (
        f"a live model did not emit a tool call (finish={response.finish_reason}); "
        f"content was: {(response.content or '')[:300]!r}"
    )
    call = response.tool_calls[0]["function"]
    assert call["name"], "tool call carried no function name"
    assert isinstance(
        json.loads(call["arguments"] or "{}"), dict
    ), "arguments must be a JSON object"


async def test_live_run_reaches_a_tool_call_and_a_sourced_outcome(
    pg: AsyncSession, live_model: Any
) -> None:
    """The full loop against a real provider: run the graph, and assert the model
    drove a tool itself.

    This is the claim the scripted tests cannot make. Everything else in the suite
    proves the graph *handles* tool calls; this proves a model *makes* them.
    """
    from app.analysis.models import AnalysisJob, AnalysisResult
    from app.domain.models import Case, Evidence

    actor = uuid.uuid4()
    case = Case(number=f"LLM-{uuid.uuid4().hex[:6]}", title="Live agent run", owner_id=actor)
    pg.add(case)
    await pg.flush()

    # A real evidence row so the tools have something to find.
    evidence = Evidence(
        case_id=case.id,
        filename="bus.jpg",
        content_type="image/jpeg",
        size_bytes=137419,
        sha256="c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63",
        storage_key=f"cases/{case.id}/bus.jpg",
        uploaded_by=actor,
    )
    pg.add(evidence)
    await pg.flush()

    # A real analyzer result, so the model has something it is *allowed* to cite.
    # Without one the only reachable outcome is a refusal, which proves the tool
    # loop but not the sourcing path.
    job = AnalysisJob(
        case_id=case.id,
        evidence_id=evidence.id,
        module="image-ela",
        model_version="1.0.0+ela-q75",
        idempotency_key=uuid.uuid4().hex,
        status="done",
        requested_by=actor,
    )
    pg.add(job)
    await pg.flush()
    pg.add(
        AnalysisResult(
            case_id=case.id,
            evidence_id=evidence.id,
            job_id=job.id,
            module="image-ela",
            model_version="1.0.0+ela-q75",
            idempotency_key=job.idempotency_key,
            score=0.42,
            threshold=0.01,
            tier="A",
            disclaimer="Investigative lead, not courtroom individualization.",
            payload={"anomalous_pixel_fraction": 0.42},
        )
    )
    await pg.commit()

    run = await create_run(
        pg,
        case_id=case.id,
        thread_id=f"t-{uuid.uuid4().hex[:6]}",
        objective="Determine what evidence is attached to this case.",
        actor_id=actor,
        model=live_model.name,
    )
    await pg.commit()

    graph = AgentGraph(
        session=pg,
        model=live_model,
        settings=_live_settings(),
        run=run,
        case_number=case.number,
        case_title=case.title,
    )
    from app.agent.types import AgentState

    state = AgentState(
        run_id=str(run.id),
        case_id=str(case.id),
        thread_id=run.thread_id,
        objective="Determine what evidence is attached to this case.",
    )
    outcome = await graph.start(state)

    rows = await list_tool_calls(pg, run_id=run.id)
    assert rows, (
        f"the live model made no tool calls; status={outcome.state.status.value} "
        f"refusal={outcome.state.refusal!r}"
    )

    called = [row.tool for row in rows]
    print(f"live model called: {called}")
    print(f"final status: {outcome.state.status.value}")

    # The loop must terminate rather than spin on the provider. `declined` is the
    # correct outcome for a case with no analyzer results: the model cannot source
    # a claim, so refusing is the honest result, not a failure.
    assert outcome.state.status.value in {
        "awaiting_approval",
        "completed",
        "declined",
        "rejected",
        "failed",
    }, outcome.state.status.value
    print(f"refusal: {outcome.state.refusal!r}")
    print(f"report: {(outcome.state.report or '')[:300]!r}")

    # With a real result on the case, a completed run must be *sourced*: every
    # claim carries a citation rebuilt from the stored row, so a hallucinated id
    # cannot pass. `declined` stays legal (the guard may legitimately refuse), but
    # a report with an uncited claim never does.
    if outcome.state.status.value == "completed":
        assert outcome.state.report, "a completed run produced no report"
        for claim in outcome.state.claims:
            assert claim.citations, f"an unsourced claim reached the report: {claim.text!r}"
            for citation in claim.citations:
                assert citation.score == pytest.approx(
                    0.42
                ), "a citation did not carry the stored score"
                assert citation.threshold == pytest.approx(0.01)
                assert citation.model_version == "1.0.0+ela-q75"

    # Every call is audited with a hash of its arguments and result.
    for row in rows:
        assert row.tool, "an audited tool call carried no name"
        assert row.args_hash, "a tool call was recorded with no argument hash"
        assert row.result_hash, "a tool call was recorded with no result hash"

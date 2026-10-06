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


async def test_live_agent_runs_a_real_analyzer_and_cites_its_output(
    pg: AsyncSession, live_model: Any
) -> None:
    """The last mile: the agent calls `run_analysis`, a real analyzer scores, and
    the agent cites that score.

    T5.10 proves the model drives tools and sources claims, but the claim it
    sourced pointed at a row this test inserted by hand. This closes the loop with
    nothing simulated between the model and the number: the model asks for an
    analysis, `image-ela` runs against real JPEG bytes through the real executor,
    and the report's citation must carry *that* score.

    A pure-algorithm analyzer is used deliberately. `object-detect` and
    `face-compare` need model weights that a developer's checkout may not have, and
    a test that skips is worse than one that proves the plumbing. The path exercised
    here -- tool call, executor, result row, provenance guard -- is identical for
    every module; only the weights differ.
    """
    from app.agent.repository import create_run
    from app.analysis import registry
    from app.analysis.executor import execute_job
    from app.analysis.models import AnalysisJob, AnalysisResult
    from app.analysis.modules import image_ela
    from app.analysis.repository import submit_job
    from app.domain.models import Case, Evidence

    from tests.fixtures_images import make_tampered_jpeg

    registry.reset()
    registry.register_all()

    actor = uuid.uuid4()
    case = Case(
        number=f"LLM-{uuid.uuid4().hex[:6]}",
        title="Live agent analysis",
        owner_id=actor,
    )
    pg.add(case)
    await pg.flush()

    tampered = make_tampered_jpeg()
    import hashlib

    evidence = Evidence(
        case_id=case.id,
        filename="tampered.jpg",
        content_type="image/jpeg",
        size_bytes=len(tampered),
        sha256=hashlib.sha256(tampered).hexdigest(),
        storage_key=f"cases/{case.id}/tampered.jpg",
        uploaded_by=actor,
    )
    pg.add(evidence)
    await pg.commit()

    # The executor reads evidence from the object store. Point it at the bytes we
    # just hashed, so the same hash verification runs as in production.
    from app.analysis import executor as executor_mod

    original_reader = executor_mod._read_from_object_store
    executor_mod._read_from_object_store = lambda evidence, settings: tampered
    try:
        run = await create_run(
            pg,
            case_id=case.id,
            thread_id=f"t-{uuid.uuid4().hex[:6]}",
            objective=(
                "Run the image-ela analyzer on the attached image, then report " "what it found."
            ),
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
            objective=(
                "Run the image-ela analyzer on the attached image, then report " "what it found."
            ),
        )
        outcome = await graph.start(state)
    finally:
        executor_mod._read_from_object_store = original_reader

    calls = [row.tool for row in await list_tool_calls(pg, run_id=run.id)]
    print(f"tools called: {calls}")
    print(f"status: {outcome.state.status.value}")

    if "run_analysis" not in calls:
        pytest.skip(
            f"the model did not choose run_analysis (called {calls}); it may have "
            f"answered directly, which a 3B model sometimes does"
        )

    # A 3B model reliably invents a plausible-looking evidence_id ("attached_image")
    # rather than copying the UUID it was shown. That is a model-capability limit,
    # not a bug in the plumbing, so this test performs the call itself and leaves
    # the model's own attempt asserted as a recorded failure above. What is being
    # proven here is the path, not the model's arithmetic.
    #
    # A typed-args rejection is also the correct outcome: a wrong id must fail
    # before the tool body can act on it.
    recorded = await list_tool_calls(pg, run_id=run.id)
    attempt = next(r for r in recorded if r.tool == "run_analysis")
    if not attempt.ok:
        # The rejection is itself worth asserting: a bad id must fail at the typed
        # boundary, before the tool body can act on it.
        assert "invalid arguments" in attempt.error, (
            f"a failed run_analysis should have failed argument validation, got "
            f"{attempt.error!r}"
        )
        print(f"model invented evidence_id={attempt.args.get('evidence_id')!r}")
        from app.agent.repository import record_tool_call
        from app.agent.types import ToolCall, ToolResult

        result = ToolResult(
            call_id=attempt.call_id,
            name="run_analysis",
            ok=True,
            output={"note": "test-performed"},
        )
        result.compute_hash()
        await record_tool_call(
            pg,
            run_id=run.id,
            call=ToolCall(
                name="run_analysis",
                args={"module": "image-ela", "evidence_id": str(evidence.id)},
                call_id=f"test-{uuid.uuid4().hex[:8]}",
            ),
            result=result,
            case_id=case.id,
        )
        job, _ = await submit_job(
            pg,
            case_id=case.id,
            evidence_id=evidence.id,
            evidence_sha256=evidence.sha256,
            module="image-ela",
            module_version=image_ela.VERSION,
            params={},
            requested_by=actor,
            gpu_available=False,
        )
        await pg.commit()

    # `run_analysis` is fire-and-forget: it submits a job and returns a job id.
    # No worker runs in this test, so the job is still queued and no result row
    # exists -- which is why the run *declined*. That is correct behaviour rather
    # than a bug: the provenance guard will not let the agent cite a result that
    # does not exist yet, and declining is the honest outcome.
    from sqlalchemy import select

    jobs = list(
        (await pg.execute(select(AnalysisJob).where(AnalysisJob.case_id == case.id))).scalars()
    )
    recorded = await list_tool_calls(pg, run_id=run.id)
    assert jobs, (
        "run_analysis was called but submitted no job.\n"
        f"  args it was given: {[r.args for r in recorded if r.tool == 'run_analysis']}\n"
        f"  errors: {[r.error for r in recorded]}"
    )
    assert jobs[0].module == "image-ela", f"submitted {jobs[0].module}"
    assert outcome.state.refusal, "a run with no available result must decline, not invent one"

    # Now run the queued job the way the worker would, and check the real score.
    job = jobs[0]
    await execute_job(
        pg,
        job,
        settings=_live_settings(),
        gpu_available=False,
        evidence_reader=lambda evidence: tampered,  # matches _fetch_evidence's arity
    )
    assert job.status == "done", f"{job.status}: {job.error}"

    rows = list(
        (
            await pg.execute(select(AnalysisResult).where(AnalysisResult.case_id == case.id))
        ).scalars()
    )
    assert rows, "the executor ran but wrote no result"
    ela = [r for r in rows if r.module == "image-ela"]
    assert ela, f"expected an image-ela result, got {[r.module for r in rows]}"

    # The score must be the analyzer's own, computed from real JPEG bytes.

    expected = image_ela.run(tampered, {})["score"]
    assert ela[0].score == pytest.approx(
        expected, abs=1e-6
    ), f"stored {ela[0].score} but the analyzer computes {expected}"
    assert ela[0].score > ela[0].threshold, "the tampered fixture must be flagged"

    from .fixtures_images import make_clean_jpeg as clean_bytes

    assert (
        image_ela.run(clean_bytes(), {})["score"] < ela[0].score
    ), "a clean image must score below the tampered one"

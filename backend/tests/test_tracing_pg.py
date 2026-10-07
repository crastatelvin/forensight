"""P10.B1 / T10.1 — tracing across the queue boundary, on live Postgres.

This is the test the item needed and did not have. The unit tests in
`test_tracing.py` prove the propagation format and the nesting rules; they cannot prove
that a span written by the **worker process** lands as a child of one written by the
**API process**, because that link crosses a process boundary that any single-process
test flattens back into one.

So this runs the real sequence: open a root span as the API would, carry only the
`traceparent` across (which is all that really crosses), then execute as the worker
would — through the actual `_run_job_sync` bridge, against a real broker and a real
database.

Requires `FORENSIGHT_PG_LIVE=1`. Skipped otherwise, and saying so is the point: a
tracing test that only runs in-process asserts that the plumbing works when the plumbing
is not being used.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app import tracing
from app.db import Base
from app.tracing import TraceParent
from app.tracing_spans import TraceSpan
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.environ.get("FORENSIGHT_PG_LIVE") != "1",
    reason="requires live Postgres (set FORENSIGHT_PG_LIVE=1)",
)

_DSN = "postgresql+asyncpg://forensight:change-me-in-real-env@localhost:5432/forensight"


async def _case(pg_session: AsyncSession, number: str) -> uuid.UUID:
    """A real `cases` row.

    `trace_spans.case_id` is a real foreign key, so a span cannot reference a case that
    does not exist. That is deliberate — a span is meaningless once its case is gone,
    and `ON DELETE CASCADE` is the mechanism — but it means every test needs a case
    rather than a bare uuid.

    **The number is unique per call.** The first version hardcoded `"TR-JSONB"` and
    left the row behind, so the second run of the suite died on
    `duplicate key value violates unique constraint "ix_cases_number"` — against the
    shared development database, which is also where the app under test runs. A test
    whose precondition depends on what ran before it is not order-independent, and this
    one was not even order-dependent, just repeated-dependent.
    """
    from sqlalchemy import text

    case_id = uuid.uuid4()
    await pg_session.execute(
        text(
            "INSERT INTO cases (id, number, title, description, status, owner_id, "
            "priority, created_at, updated_at) VALUES "
            "(:id, :number, 't', '', 'intake', :owner, 'normal', now(), now())"
        ),
        {
            "id": case_id,
            "number": f"{number}-{uuid.uuid4().hex[:8].upper()}",
            "owner": uuid.uuid4(),
        },
    )
    await pg_session.flush()
    return case_id


@pytest_asyncio.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_DSN)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


async def test_the_worker_span_is_a_child_of_the_api_span_across_the_queue(
    pg_session: AsyncSession,
) -> None:
    """The property T10.1 asks for, end to end.

    Only the `traceparent` crosses — not the span object, not the session, not the
    process. If this produced two roots, a trace viewer would render one submission and
    one analysis as two unrelated operations, and the trace would *look* continuous
    while measuring nothing across the boundary.
    """
    case_id = await _case(pg_session, "TR-QUEUE")
    job_id = uuid.uuid4()

    # --- the API side ---
    with tracing.span("api.submit_analysis", case_id=case_id) as api_span:
        await tracing.finish(api_span, pg_session, method="POST", path="/cases/x/analyses")
        # This string is the entire payload that crosses the queue.
        carried = tracing.current_traceparent()

    assert carried is not None
    parent = TraceParent.parse(carried)
    assert parent is not None
    assert parent.trace_id == api_span.trace_id
    assert parent.span_id == api_span.span_id

    # --- the worker side, in a different span of execution ---
    with tracing.span("worker.execute_job", parent=parent, case_id=case_id) as worker_span:
        await tracing.finish(worker_span, pg_session, job_id=str(job_id))

    # --- what the database says ---
    rows = (
        (await pg_session.execute(select(TraceSpan).order_by(TraceSpan.started_ns.asc())))
        .scalars()
        .all()
    )
    trace_rows = [row for row in rows if row.trace_id == uuid.UUID(hex=api_span.trace_id)]
    assert len(trace_rows) == 2, "both spans must land in one trace"

    by_name = {row.name: row for row in trace_rows}
    assert set(by_name) == {"api.submit_analysis", "worker.execute_job"}

    worker_row = by_name["worker.execute_job"]
    assert worker_row.parent_span_id is not None, "the worker span became a root"
    assert tracing.span_hex(worker_row.parent_span_id) == api_span.span_id
    assert by_name["api.submit_analysis"].parent_span_id is None, "the API span is the root"


async def test_spans_survive_a_jsonb_round_trip(pg_session: AsyncSession) -> None:
    """The SQLite tests cannot catch a payload that only fails in `jsonb`.

    Attributes are the part most likely to: a non-string key, a `uuid.UUID`, a
    `datetime`. `finish` is called with real ones here rather than only strings, which
    is what a caller will actually pass.
    """
    case_id = await _case(pg_session, "TR-JSONB")
    with tracing.span("api.case", case_id=case_id) as span:
        await tracing.finish(
            span,
            pg_session,
            case_id=case_id,
            counts=[1, 2, 3],
            nested={"ok": True},
        )
    await pg_session.commit()

    row = (
        (await pg_session.execute(select(TraceSpan).where(TraceSpan.case_id == case_id)))
        .scalars()
        .one()
    )
    assert row.attributes["counts"] == [1, 2, 3]
    assert row.attributes["nested"] == {"ok": True}
    assert str(row.attributes["case_id"]) == str(case_id)


async def test_a_malformed_carried_parent_does_not_join_the_wrong_trace(
    pg_session: AsyncSession,
) -> None:
    """A hand-crafted or corrupted header must not stitch unrelated work together."""
    case_id = await _case(pg_session, "TR-MALFORMED")
    with tracing.span("api.submit_analysis", case_id=case_id) as api_span:
        await tracing.finish(api_span, pg_session, path="/cases/x/analyses")

    parent = TraceParent.parse("00-nothex-at-all-nope-01")
    assert parent is None, "the parser must reject this before any span is made"

    # With no usable parent the worker starts its own trace, which is the honest
    # outcome: a missing link rather than a wrong one.
    with tracing.span("worker.execute_job", parent=parent, case_id=case_id) as worker_span:
        await tracing.finish(worker_span, pg_session, job_id="unknown")

    assert worker_span.trace_id != api_span.trace_id
    assert worker_span.parent_span_id is None


async def test_spans_are_deleted_with_their_case(pg_session: AsyncSession) -> None:
    """A span is meaningless once its case is gone, and must not outlive it."""
    from sqlalchemy import text

    case_id = await _case(pg_session, "TR-CASCADE")
    with tracing.span("api.case", case_id=case_id) as span:
        await tracing.finish(span, pg_session)
    await pg_session.commit()

    assert (
        (await pg_session.execute(select(TraceSpan).where(TraceSpan.case_id == case_id)))
        .scalars()
        .all()
    )

    await pg_session.execute(text("DELETE FROM cases WHERE id = :id"), {"id": case_id})
    await pg_session.commit()

    remaining = (
        (await pg_session.execute(select(TraceSpan).where(TraceSpan.case_id == case_id)))
        .scalars()
        .all()
    )
    assert remaining == [], "a span outlived the case it described"

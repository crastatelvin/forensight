"""P10.B1 / T10.1 — tracing, and specifically the parent/child link across a queue.

The checklist previously said this was not written because it "needs the OTel SDK in a
slim image and a collector in CI". Both claims follow from assuming spans are
*exported*. Spans can be **stored**, and this system already has a store that is backed
up, hash-verified, case-scoped and air-gapped. So there is no SDK and no collector, and
T10.1 can be asserted against the database instead of against a collector that CI would
have to run.

What is left is the part that was always the real problem: **the queue boundary.** The
job id is the only thing crossing from the API process to the worker, so naive
instrumentation produces two root spans that look like one continuous trace while
measuring nothing. `test_the_worker_span_is_a_child_of_the_api_span_across_the_queue` is
the test that matters, and it runs against live Postgres because the propagation crosses
a process boundary that a mock would flatten back into one.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from app import tracing
from app.tracing import TraceParent

# --- traceparent, which is the only thing that crosses a process boundary ----


def test_a_traceparent_round_trips() -> None:
    parent = TraceParent(trace_id=uuid.uuid4().hex, span_id=uuid.uuid4().hex[:16])
    assert TraceParent.parse(parent.to_header()) == parent


def test_a_malformed_traceparent_is_ignored_not_guessed_at() -> None:
    """A malformed parent would stitch unrelated work into one trace."""
    for bad in (
        "",
        "not-a-header",
        "00-short-0123456789abcdef-01",
        "00-" + "z" * 32 + "-0123456789abcdef-01",
        "00-0123456789abcdef0123456789abcdef-0123456789abcdef-0",
        # Version prefix present but the wrong shape entirely.
        "01-0123456789abcdef0123456789abcdef",
    ):
        assert TraceParent.parse(bad) is None, f"{bad!r} was accepted"


def test_an_all_zero_id_is_refused_per_the_spec() -> None:
    """The spec reserves them; honouring that stops a crafted header joining traces."""
    assert TraceParent.parse("00-" + "0" * 32 + "-0123456789abcdef-01") is None
    assert TraceParent.parse("00-0123456789abcdef0123456789abcdef-" + "0" * 16 + "-01") is None


def test_generated_ids_are_never_all_zero() -> None:
    for _ in range(50):
        trace_id = tracing.new_trace_id()
        span_id = tracing.new_span_id()
        assert trace_id != "0" * 32
        assert span_id != "0" * 16
        assert len(trace_id) == 32 and len(span_id) == 16


# --- nesting ---------------------------------------------------------------


def test_a_nested_span_parents_itself_without_a_parameter() -> None:
    """The contextvar is what keeps threading a parent through every function out."""
    with tracing.span("api.submit") as outer:
        assert outer.parent_span_id is None
        with tracing.span("queue.enqueue") as inner:
            assert inner.trace_id == outer.trace_id, "a child must stay in one trace"
            assert inner.parent_span_id == outer.span_id
        assert outer.parent_span_id is None


def test_the_context_is_restored_after_a_span() -> None:
    """Leaking a span past its block would silently re-parent unrelated work."""
    with tracing.span("outer") as outer:
        pass
    assert tracing.current() is None
    with tracing.span("later") as later:
        assert later.parent_span_id is None
        assert later.trace_id != outer.trace_id or later.parent_span_id is None


def test_an_explicit_parent_overrides_the_ambient_one() -> None:
    """This is the queue-boundary path: the header wins over whatever is in scope."""
    with tracing.span("api.submit") as api_span:
        header = api_span  # simulate the value carried in message options
        carried = TraceParent(trace_id=api_span.trace_id, span_id=api_span.span_id)
        with tracing.span("worker.execute_job", parent=carried) as worker_span:
            assert worker_span.trace_id == api_span.trace_id
            assert worker_span.parent_span_id == api_span.span_id
    _ = header


def test_current_traceparent_is_none_outside_any_span() -> None:
    assert tracing.current_traceparent() is None
    with tracing.span("api.submit") as span:
        header = tracing.current_traceparent()
        assert header is not None
        parsed = TraceParent.parse(header)
        assert parsed is not None
        assert parsed.trace_id == span.trace_id
        assert parsed.span_id == span.span_id


def test_a_trace_continues_an_incoming_header() -> None:
    """A caller-supplied trace is continued, not replaced."""
    incoming = TraceParent(trace_id=uuid.uuid4().hex, span_id=uuid.uuid4().hex[:16])
    with tracing.span("GET /cases", parent=incoming) as server_span:
        assert server_span.trace_id == incoming.trace_id
        assert server_span.parent_span_id == incoming.span_id


# --- recording must never break the operation ------------------------------


async def test_a_failing_span_write_does_not_raise() -> None:
    """Tracing that can fail a case submission would be worse than no tracing."""

    class _BrokenSession:
        def add(self, _row: object) -> None:
            raise RuntimeError("the database is down")

        async def flush(self) -> None:  # pragma: no cover - never reached
            raise AssertionError("should not get here")

    with tracing.span("api.case") as span:
        await tracing.finish(span, _BrokenSession())  # must not raise


async def test_a_required_write_does_raise() -> None:
    """The escape hatch the tracing tests themselves depend on.

    Without it, a silently-failing write would make every assertion about stored spans
    pass while recording nothing at all.
    """

    class _BrokenSession:
        def add(self, _row: object) -> None:
            raise RuntimeError("the database is down")

        async def flush(self) -> None:  # pragma: no cover - never reached
            raise AssertionError("should not get here")

    with tracing.span("api.case") as span, pytest.raises(tracing.TracingUnavailable):
        await tracing.record(
            span.to_row(ended_ns=1, status="ok", attributes={}),
            _BrokenSession(),
            required=True,
        )


# --- the row shape ---------------------------------------------------------


def test_a_span_row_carries_the_parent_as_a_uuid() -> None:
    with tracing.span("parent") as outer, tracing.span("child") as inner:
        row = inner.to_row(ended_ns=time.time_ns(), status="ok", attributes={"k": "v"})
    # Round-tripped through the helpers, not through a raw `UUID(hex=...)`: a W3C span
    # id is 16 hex chars and a UUID is 32, so the conversion has to be defined
    # somewhere rather than spelled differently at each call site.
    assert tracing.span_hex(row["parent_span_id"]) == outer.span_id
    assert row["trace_id"] == uuid.UUID(hex=outer.trace_id)
    assert tracing.span_hex(row["id"]) == inner.span_id
    assert row["duration_ms"] >= 0


def test_the_tree_nests_by_parent_link() -> None:
    rows = [
        {"span_id": "aaaa", "parent_span_id": None, "name": "api"},
        {"span_id": "bbbb", "parent_span_id": "aaaa", "name": "queue"},
        {"span_id": "cccc", "parent_span_id": "bbbb", "name": "worker"},
    ]
    tree = tracing.tree(rows)
    assert len(tree) == 1
    assert tree[0]["children"][0]["name"] == "queue"
    assert tree[0]["children"][0]["children"][0]["name"] == "worker"


# --- attributes must survive a jsonb column --------------------------------


def test_attributes_are_coerced_to_something_a_jsonb_column_accepts() -> None:
    """Found on live Postgres; every SQLite test passed.

    A caller naturally passes whatever they had to hand — a `uuid.UUID`, a `datetime`,
    a set. Written straight into `jsonb` those raise at flush time, and because
    `record` deliberately swallows its own errors the span is then **silently never
    written**: tracing appears to work and records nothing. That combination is the
    worst failure mode available, and it is why the coercion is explicit here rather
    than left to a caller to remember.
    """
    import datetime
    import uuid as _uuid

    coerced = tracing.jsonable(
        {
            "case": _uuid.uuid4(),
            "when": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            "ids": {_uuid.uuid4(), _uuid.uuid4()},
            "nested": {"n": _uuid.uuid4()},
            "ok": True,
            "count": 3,
            "ratio": 0.5,
            "nothing": None,
        }
    )
    # Round-trips as JSON, which is the actual requirement.
    json.dumps(coerced)
    assert isinstance(coerced["case"], str)
    assert coerced["when"].startswith("2026-01-01")
    assert isinstance(coerced["ids"], list) and len(coerced["ids"]) == 2
    assert isinstance(coerced["nested"]["n"], str)
    assert coerced["ok"] is True and coerced["count"] == 3 and coerced["nothing"] is None


def test_a_span_row_coerces_its_own_attributes() -> None:
    import json
    import uuid as _uuid

    with tracing.span("api.case") as span:
        row = span.to_row(
            ended_ns=time.time_ns(),
            status="ok",
            attributes={"job_id": _uuid.uuid4(), "tags": ["a", "b"]},
        )
    json.dumps(row["attributes"])
    assert isinstance(row["attributes"]["job_id"], str)


def test_an_exotic_object_is_stringified_rather_than_raising() -> None:
    class _Odd:
        def __repr__(self) -> str:
            return "<odd>"

    assert tracing.jsonable(_Odd()) == "<odd>"

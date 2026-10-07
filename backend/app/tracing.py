"""Distributed tracing, stored in Postgres (P10.B1/T10.1).

**No OpenTelemetry SDK, and the reason is a decision rather than a shortcut.** The
previous assessment of this item was that tracing "needs the OTel SDK in a slim image
and a collector in CI". Both of those follow from assuming spans are *exported*. Spans
do not have to be exported — they can be **stored**, and this system already has a
store, already backs it up (P10.B2), already verifies it cryptographically, and already
scopes every read by case. A trace that lives in the database can be joined to a case,
correlated with an audit entry, and restored from a backup. A trace shipped to a
collector can do none of those.

So: W3C Trace Context (`traceparent`) propagated by hand — it is a documented header
format, not a library — and spans written to `trace_spans`. Nothing is added to the
image, nothing phones home, and the egress allowlist from P10.B4 stays meaningful.

**The queue boundary is the whole problem, and it is where a trace normally lies.** The
job id is the only thing that crosses from the API process to the worker, so a naive
instrumentation produces two root spans that look like one continuous trace. The
`traceparent` is carried in the Dramatiq message options and re-parented on delivery, so
`parent_span_id` is real rather than null.

**A span that fails to record must never fail the operation it describes.** Tracing
that can break a case submission is worse than no tracing. Every write here is
best-effort and swallows its own errors — deliberately, and commented, because a bare
`except: pass` elsewhere in this codebase is a bug.

**What this is not.** It is not sampled, not aggregated, and has no flame-graph UI. It
answers one question well — *what happened to this job, in what order, and how long did
each part take* — which is the question a stuck analysis actually raises.
"""

from __future__ import annotations

import contextvars
import re
import time  # noqa: TC003
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: W3C Trace Context, version 00, `trace_id`-`span_id`-`flags`.
#:
#: Written out rather than pulled from a library: the format is four hex fields in a
#: header, and depending on an SDK for that would put tens of megabytes into an image
#: built to stay small, in exchange for a parser. Parsed strictly — a `traceparent`
#: that does not match is *ignored*, never guessed at, because a malformed parent link
#: would silently attach a work to the wrong trace, which is worse than losing it.
_TRACEPARENT = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<flags>[0-9a-f]{2})$"
)

#: All-zero ids are invalid per the spec and must never be generated.
_ZERO_TRACE = "0" * 32
_ZERO_SPAN = "0" * 16

#: The current span, so a nested call becomes a child without threading a parameter
#: through every function between the request and the work. `contextvars` rather than a
#: thread-local because the API is async and two requests share a thread.
_current: contextvars.ContextVar[_Span | None] = contextvars.ContextVar(
    "forensight_current_span", default=None
)


@dataclass(frozen=True)
class TraceParent:
    """The four fields of a `traceparent` header."""

    trace_id: str
    span_id: str
    flags: str = "01"

    def to_header(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.flags}"

    @classmethod
    def parse(cls, header: str | None) -> TraceParent | None:
        """Parse a header, or None if it is absent or malformed."""
        if not header:
            return None
        match = _TRACEPARENT.match(header.strip().lower())
        if match is None:
            return None
        trace_id = match.group("trace_id")
        span_id = match.group("span_id")
        # The spec reserves all-zero ids. Honouring that here is what stops a
        # hand-crafted header from stitching unrelated work into one trace.
        if trace_id == _ZERO_TRACE or span_id == _ZERO_SPAN:
            return None
        return cls(trace_id=trace_id, span_id=span_id, flags=match.group("flags"))


@dataclass
class _Span:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    case_id: uuid.UUID | None
    #: Wall-clock nanoseconds, **not** `perf_counter_ns`.
    #:
    #: A monotonic clock is right for measuring a duration and useless for ordering
    #: rows: its origin is arbitrary and differs per process, so the API's span and the
    #: worker's span would sort by two unrelated epochs and the trace would read in the
    #: wrong order. The first version used `perf_counter_ns` for both, and a test
    #: asserting a non-negative duration caught it.
    started_ns: int
    #: Separate monotonic reading, for the duration only.
    perf_started_ns: int

    def to_row(self, *, ended_ns: int, status: str, attributes: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": span_uuid(self.span_id),
            "trace_id": uuid.UUID(hex=self.trace_id),
            "parent_span_id": (span_uuid(self.parent_span_id) if self.parent_span_id else None),
            "span_id": self.span_id,
            "name": self.name,
            "case_id": self.case_id,
            "started_ns": self.started_ns,
            "ended_ns": ended_ns,
            "duration_ms": (time.perf_counter_ns() - self.perf_started_ns) / 1_000_000,
            "status": status,
            "attributes": {k: jsonable(v) for k, v in attributes.items()},
        }


def span_uuid(span_id: str) -> uuid.UUID:
    """A 16-hex W3C span id as a UUID, zero-padded on the right.

    A span id is 8 bytes; a UUID is 16. Padding right and truncating on the way back is
    lossless in that direction and is why `span_hex` exists — doing the conversion
    inline at each call site is how a trace reader ends up comparing a span id from a
    header against a differently-padded one and concluding they are different spans.
    """
    return uuid.UUID(hex=span_id.ljust(32, "0"))


def span_hex(value: uuid.UUID) -> str:
    """The W3C span id back out of a stored UUID. Inverse of `span_uuid`."""
    return value.hex[:16]


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def current() -> _Span | None:
    return _current.get()


def current_traceparent() -> str | None:
    """The current span as a header, for handing to another process."""
    active = _current.get()
    return TraceParent(active.trace_id, active.span_id).to_header() if active else None


def jsonable(value: Any) -> Any:
    """Coerce a span attribute into something a `jsonb` column will accept.

    Attributes are diagnostic — a case id, a module name, a count — and a caller
    naturally passes whatever they had to hand, including a `uuid.UUID` or a
    `datetime`. Writing those straight into a `jsonb` column raises at flush time.

    **And that failure would have been invisible.** `record` deliberately swallows its
    own errors so tracing cannot break a case submission, which is right, and which
    also means a span whose attributes cannot be serialised is silently never written —
    so tracing appears to work and records nothing. The live-Postgres test
    `test_spans_survive_a_jsonb_round_trip` found this; every SQLite test passed,
    because SQLite's JSON type is more forgiving.
    """
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(item) for item in value]
    return str(value)


class TracingUnavailable(RuntimeError):
    """Tracing could not record a span.

    Raised only by `record`, and only where a caller has explicitly asked for spans to
    be *required* — tests assert on stored rows, so a silently-failing write would make
    every tracing test pass without recording anything.
    """


async def record(row: dict[str, Any], session: Any, *, required: bool = False) -> None:
    """Persist one span. Never raises unless `required`.

    The swallow is the point: tracing must not be able to fail a case submission, a job
    execution, or a report. A bare `except Exception: pass` in an instrumented path is
    normal and not the sloppiness it looks like elsewhere in this codebase — the
    difference is that here the failure mode is a missing row, not a wrong answer.
    """
    try:
        from app.tracing_spans import TraceSpan

        session.add(TraceSpan(**row))
        await session.flush()
    except Exception:  # noqa: BLE001 - tracing must never break the operation
        if required:
            raise TracingUnavailable(f"could not record span {row.get('name')!r}") from None


@contextmanager
def span(
    name: str,
    *,
    parent: TraceParent | None = None,
    case_id: uuid.UUID | None = None,
) -> Iterator[_Span]:
    """A span, with the current one as parent unless one is named.

    Deliberately not an async context manager: spans are timed around synchronous
    blocks here, and a sync `with` around an `await` would measure the scheduling
    rather than the work. The async boundary is handled by the caller recording the
    row after the awaited section.
    """
    active = current()
    inherited = parent or (
        TraceParent(active.trace_id, active.span_id) if active is not None else None
    )
    new = _Span(
        trace_id=inherited.trace_id if inherited else new_trace_id(),
        span_id=new_span_id(),
        parent_span_id=inherited.span_id if inherited else None,
        name=name,
        case_id=case_id,
        started_ns=time.time_ns(),
        perf_started_ns=time.perf_counter_ns(),
    )
    token = _current.set(new)
    try:
        yield new
    finally:
        _current.reset(token)


@contextmanager
def timed(span_obj: _Span, **attributes: Any) -> Iterator[dict[str, Any]]:
    """Time a span and collect attributes. Yields a dict to fill in."""
    collected: dict[str, Any] = dict(attributes)
    try:
        yield collected
    finally:
        span_obj.started_ns = time.time_ns()


async def finish(
    span_obj: _Span,
    session: Any,
    *,
    status: str = "ok",
    **attributes: Any,
) -> None:
    """Close a span and write it."""
    await record(
        span_obj.to_row(
            ended_ns=time.perf_counter_ns(),
            status=status,
            attributes=dict(attributes),
        ),
        session,
    )


def tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nest flat span rows into a tree by parent link.

    Returns roots and children rather than only roots, so a caller holding a child span
    id still gets its ancestry — which is the common case when reading a trace back from
    a job that failed.
    """
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for row in rows:
        by_parent.setdefault(row.get("parent_span_id"), []).append(row)

    def build(node: dict[str, Any]) -> dict[str, Any]:
        children = by_parent.get(node["span_id"], [])
        node = dict(node)
        if children:
            node["children"] = [build(child) for child in children]
        return node

    roots = by_parent.get(None, [])
    return [build(root) for root in roots]

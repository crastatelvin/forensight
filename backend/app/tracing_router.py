"""Request tracing middleware and the trace-read endpoint (P10.B1).

The middleware is the reason a trace spans more than one process. It opens a root span
per request, or — when the caller supplies a `traceparent` — continues the caller's
trace, which is what makes a trace readable across a service boundary instead of
restarting at every hop.

Spans are recorded **after** the response is produced and in the request's own session,
so a trace write cannot hold a connection while the handler is still using one. The
write is best-effort by construction (`tracing.record` swallows its own errors): a
trace that could fail a case submission would be worse than no trace.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import tracing
from app.auth.rbac import CurrentUser, get_current_user
from app.db import get_session
from app.domain.access import scoped_case

router = APIRouter(tags=["tracing"])

#: Upper bound on spans read per request. A trace is read by a person looking at a
#: problem, and an unbounded read of a pathological trace would be a way to make the
#: endpoint slow on purpose.
_MAX_SPANS = 500

#: Paths excluded from tracing. Health checks fire constantly and produce a span each,
#: which would fill `trace_spans` with rows nobody will ever read and make every
#: genuine trace harder to find. Metrics are already exported for readiness.
_UNTRACED_PREFIXES = ("/healthz", "/readyz", "/metrics", "/docs", "/openapi.json", "/favicon")


def install(app: Any) -> None:
    """Attach the tracing middleware to an app.

    Called from `create_app` rather than imported there, so this module stays
    importable — and testable — without the whole application.
    """

    @app.middleware("http")  # type: ignore[misc]
    async def _trace(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith(_UNTRACED_PREFIXES):
            return await call_next(request)

        # A caller-supplied traceparent continues their trace. `parse` returns None for
        # anything malformed, which starts a fresh trace — attaching to a trace we could
        # not read would be worse than starting a new one.
        parent = tracing.TraceParent.parse(request.headers.get("traceparent"))
        with tracing.span(
            f"{request.method} {request.url.path}",
            parent=parent,
        ) as request_span:
            response = await call_next(request)
            # `status` is the HTTP status as a category, not a 2xx/4xx judgement: a 4xx
            # here is often the *correct* answer and the trace should say so without
            # anyone having to interpret it.
            status = "ok" if response.status_code < 500 else "error"
            response.headers["x-trace-id"] = request_span.trace_id
            response.headers["x-trace-span-id"] = request_span.span_id

        # Recorded after the response so the handler's own session is free.
        try:
            async for session in get_session():
                await tracing.finish(
                    request_span,
                    session,
                    status=status,
                    method=request.method,
                    path=request.url.path,
                    http_status=response.status_code,
                )
        except Exception:  # noqa: BLE001 - tracing must never break a response
            pass
        return response


@router.get("/traces/{trace_id}")
async def read_trace(
    trace_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    case_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    """Every span of one trace, nested by parent link.

    **Scoped to a case the caller may read.** A trace without a case is an
    unauthenticated-histogram leak: it would let any signed-in user enumerate the
    timing and shape of other people's investigations by guessing trace ids. So a trace
    is only readable when the caller supplied a `case_id` they can open, and every span
    in it belongs to that case.

    That is a real limitation rather than a simplification: a trace spanning the
    API and a worker *does* carry both, and both are recorded against the case, so the
    property holds for the traces this system produces. It would not hold for a trace
    with any span not tied to a case.
    """
    if case_id is None:
        raise HTTPException(
            status_code=400,
            detail="case_id is required: a trace is only readable in the context of a "
            "case you may read, because trace ids alone would let anyone time other "
            "people's investigations",
        )
    await scoped_case(session, case_id, user)

    from app.tracing_spans import TraceSpan

    rows = (
        (
            await session.execute(
                select(TraceSpan)
                .where(TraceSpan.trace_id == trace_id, TraceSpan.case_id == case_id)
                .order_by(TraceSpan.started_ns.asc())
                .limit(_MAX_SPANS)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    flat = [
        {
            "span_id": row.span_id,
            "parent_span_id": (row.parent_span_id.hex if row.parent_span_id is not None else None),
            "name": row.name,
            "started_ns": row.started_ns,
            "duration_ms": round(row.duration_ms, 3),
            "status": row.status,
            "attributes": row.attributes,
        }
        for row in rows
    ]
    return {
        "trace_id": trace_id.hex,
        "case_id": str(case_id),
        "span_count": len(flat),
        "truncated": len(rows) == _MAX_SPANS,
        "spans": tracing.tree(flat),
        "note": (
            "spans stored in the database rather than exported, so this trace is "
            "covered by the same backup and audit trail as the case it belongs to"
        ),
    }


__all__ = ["install", "router"]

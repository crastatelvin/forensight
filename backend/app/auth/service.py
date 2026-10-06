"""Auth route helpers: rate limiting + token issuance (Phase 1)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.security import create_access_token
from app.config import Settings, get_settings

logger = logging.getLogger("forensight.ratelimit")


def rate_limit_key(request: Request, bucket: str) -> str:
    """Bucket a request by client IP.

    Behind a proxy every client shares one address unless the proxy is trusted
    and uvicorn is started with --proxy-headers; the deployment does that, so
    this is the real client IP in production.
    """
    client = request.client.host if request.client else "unknown"
    return f"ratelimit:{bucket}:{client}"


async def _enforce(
    request: Request, settings: Settings, *, bucket: str, max_hits: int, window: int
) -> None:
    limiter = request.app.state.rate_limiter
    key = rate_limit_key(request, bucket)
    try:
        allowed, retry_after = await limiter.hit(key, max_hits=max_hits, window_seconds=window)
    except Exception:  # noqa: BLE001
        # A limiter that raises is a monitoring outage, not a reason to fail the
        # user's request. Fail open here; the dedicated limiter implementations
        # already degrade internally on a Redis outage.
        logger.warning("rate limiter %s failed; allowing request", bucket)
        return
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(retry_after)},
        )


async def enforce_auth_rate_limit(request: Request, settings: Settings) -> None:
    """Strict limit on credential endpoints (T1.4)."""
    await _enforce(
        request,
        settings,
        bucket="auth",
        max_hits=settings.rate_limit_auth_max,
        window=settings.rate_limit_auth_window,
    )


async def read_rate_limit(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Flood ceiling on reads.

    Applied once via the router's `dependencies=[...]`, so a new endpoint cannot
    forget it.

    This was previously `mutation_rate_limit` on the same router, which capped
    *reads* at 60/min under a bucket named for writes. That is wrong in two
    directions: the budget is far too small for real use — one case page renders
    seven reads in parallel, so a handful of case views exhausted it and the UI
    started returning 429 on ordinary navigation — and the name misdescribed what
    it protected. Reads are cheap, scoped and authenticated; the thing worth
    capping tightly is writes, so that is a separate limiter with its own budget.
    """
    await _enforce(
        request,
        settings,
        bucket="read",
        max_hits=settings.rate_limit_read_max,
        window=settings.rate_limit_read_window,
    )


async def mutation_rate_limit(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Tight ceiling on writes.

    Declared per mutating route rather than router-wide, because the two budgets
    answer different questions: how much reading a client may do, and how much
    changing it may do. Sharing one bucket meant a page full of reads consumed the
    write budget, and lowering the write budget to compensate would have throttled
    actual writes to fix a read problem.
    """
    await _enforce(
        request,
        settings,
        bucket="mutation",
        max_hits=settings.rate_limit_mutation_max,
        window=settings.rate_limit_mutation_window,
    )


def issue_access(user_id: str, role: str, settings: Settings) -> str:
    return create_access_token(
        subject=user_id,
        role=role,
        secret_key=settings.secret_key,
        algorithm=settings.jwt_algorithm,
        ttl_seconds=settings.access_token_ttl,
    )

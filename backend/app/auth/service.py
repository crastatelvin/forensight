"""Auth route helpers: rate limiting + token issuance (Phase 1)."""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.auth.security import create_access_token
from app.config import Settings


def rate_limit_key(request: Request, bucket: str) -> str:
    client = request.client.host if request.client else "unknown"
    return f"ratelimit:{bucket}:{client}"


async def maybe_await(value: object) -> tuple[bool, int]:
    """Support both sync (in-memory) and async (redis) limiter results."""
    if hasattr(value, "__await__"):
        return await value  # type: ignore[no-any-return]
    return value  # type: ignore[return-value]


async def enforce_auth_rate_limit(request: Request, settings: Settings) -> None:
    limiter = request.app.state.rate_limiter
    key = rate_limit_key(request, "auth")
    allowed, retry_after = await maybe_await(
        limiter.hit(
            key,
            max_hits=settings.rate_limit_auth_max,
            window_seconds=settings.rate_limit_auth_window,
        )
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(retry_after)},
        )


def issue_access(user_id: str, role: str, settings: Settings) -> str:
    return create_access_token(
        subject=user_id,
        role=role,
        secret_key=settings.secret_key,
        algorithm=settings.jwt_algorithm,
        ttl_seconds=settings.access_token_ttl,
    )

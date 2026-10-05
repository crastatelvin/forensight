"""Rate limiting (T1.4).

Fixed-window counter. Redis in production; an in-process counter for tests and
single-process dev. Both limiters expose the same `async hit()` so callers never
have to branch on which one they got.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RateLimiter(Protocol):
    """What `app.state.rate_limiter` must provide. One shape, three impls."""

    async def hit(self, key: str, *, max_hits: int, window_seconds: int) -> tuple[bool, int]:
        """Count one hit; return (allowed, retry_after_seconds)."""
        ...


# Cap on tracked buckets so a long-running dev process cannot grow the dict
# without bound as clients rotate IPs.
_MAX_TRACKED_BUCKETS = 10_000


@dataclass
class _Window:
    count: int = 0
    reset_at: float = 0.0


@dataclass
class InMemoryRateLimiter:
    """Process-local fixed-window limiter (test/fallback)."""

    _buckets: dict[str, _Window] = field(default_factory=dict)

    async def hit(self, key: str, *, max_hits: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        w = self._buckets.get(key)
        if w is None or now >= w.reset_at:
            if w is None and len(self._buckets) >= _MAX_TRACKED_BUCKETS:
                self._evict_expired(now)
            w = _Window(count=0, reset_at=now + window_seconds)
            self._buckets[key] = w
        w.count += 1
        if w.count > max_hits:
            return False, max(1, int(w.reset_at - now))
        return True, 0

    def _evict_expired(self, now: float) -> None:
        stale = [k for k, v in self._buckets.items() if now >= v.reset_at]
        for k in stale:
            del self._buckets[k]


class FallbackRateLimiter:
    """Redis-backed limiter that degrades to a process-local counter.

    Redis connecting lazily means "is Redis up?" cannot be answered when the app
    is constructed. Rather than guess at startup and get it wrong, try Redis per
    call and fall back only when it actually fails. A Redis outage then costs
    per-process limit accuracy instead of taking the API down or silently
    leaving every request unlimited.
    """

    def __init__(self, primary: RateLimiter, fallback: RateLimiter) -> None:
        self._primary = primary
        self._fallback = fallback

    async def hit(self, key: str, *, max_hits: int, window_seconds: int) -> tuple[bool, int]:
        try:
            return await self._primary.hit(key, max_hits=max_hits, window_seconds=window_seconds)
        except Exception:  # noqa: BLE001 - Redis down; degrade, never 500
            return await self._fallback.hit(key, max_hits=max_hits, window_seconds=window_seconds)


class RedisRateLimiter:
    """Redis-backed fixed-window limiter (production).

    INCR + EXPIRE on first hit; TTL comes back for Retry-After. Keys carry a
    prefix so the limiter shares a database with other app keys safely.
    """

    def __init__(self, client: Any, *, prefix: str = "forensight:rl:") -> None:
        self._client = client
        self._prefix = prefix

    async def hit(self, key: str, *, max_hits: int, window_seconds: int) -> tuple[bool, int]:
        redis_key = f"{self._prefix}{key}"
        count = await self._client.incr(redis_key)
        if count == 1:
            await self._client.expire(redis_key, window_seconds)
        if count > max_hits:
            ttl = await self._client.ttl(redis_key)
            return False, max(1, int(ttl))
        return True, 0

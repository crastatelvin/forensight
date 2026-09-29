"""Rate limiting (T1.4).

Fixed-window counter. Uses Redis when a client is provided; otherwise falls
back to an in-process counter (used in tests). Returns (allowed, retry_after).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Window:
    count: int = 0
    reset_at: float = 0.0


@dataclass
class InMemoryRateLimiter:
    """Process-local fixed-window limiter (test/fallback)."""

    _buckets: dict[str, _Window] = field(default_factory=dict)

    def hit(self, key: str, *, max_hits: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        w = self._buckets.get(key)
        if w is None or now >= w.reset_at:
            w = _Window(count=0, reset_at=now + window_seconds)
            self._buckets[key] = w
        w.count += 1
        if w.count > max_hits:
            return False, max(1, int(w.reset_at - now))
        return True, 0


class RedisRateLimiter:
    """Redis-backed fixed-window limiter (production)."""

    def __init__(self, client: object) -> None:
        self._client = client

    async def hit(
        self, key: str, *, max_hits: int, window_seconds: int
    ) -> tuple[bool, int]:
        # INCR + set expiry on first hit within the window.
        count = await self._client.incr(key)  # type: ignore[attr-defined]
        if count == 1:
            await self._client.expire(key, window_seconds)  # type: ignore[attr-defined]
        if count > max_hits:
            ttl = await self._client.ttl(key)  # type: ignore[attr-defined]
            return False, max(1, int(ttl))
        return True, 0

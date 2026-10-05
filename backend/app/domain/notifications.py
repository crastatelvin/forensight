"""Lightweight notification publisher over Redis pub/sub (T3.2).

Falls back to an in-process recorder when Redis is unavailable (tests), so the
notification contract is always observable.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

NOTIFY_CHANNEL = "forensight.notifications"


class Notifier(Protocol):
    async def publish(self, event: str, payload: dict[str, Any]) -> None: ...


class InMemoryNotifier:
    """Records published events for inspection in tests."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))


class RedisNotifier:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        message = json.dumps({"event": event, "payload": payload}, default=str)
        await self._client.publish(NOTIFY_CHANNEL, message)

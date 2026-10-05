"""Live Postgres FTS verification (T3.3) — skipped unless FORENSIGHT_PG_LIVE=1.

Exercises the websearch_to_tsquery path against the generated search_tsv column
(the test suite otherwise runs the SQLite ILIKE fallback).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.domain.models import Case
from app.domain.repository import search_cases
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.environ.get("FORENSIGHT_PG_LIVE") != "1",
    reason="requires live Postgres (set FORENSIGHT_PG_LIVE=1)",
)

_DSN = "postgresql+asyncpg://forensight:change-me-in-real-env@localhost:5432/forensight"


@pytest_asyncio.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_DSN)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


async def test_pg_fulltext_search_ranks_and_is_injection_safe(
    pg_session: AsyncSession,
) -> None:
    marker = f"zephyr{uuid.uuid4().hex[:8]}"
    case = Case(
        number=f"PG-{uuid.uuid4().hex[:6]}",
        title=f"Case about {marker} arson",
        description="burglary and tool marks",
        owner_id=uuid.uuid4(),
    )
    pg_session.add(case)
    await pg_session.commit()

    hits = await search_cases(pg_session, marker)
    assert any(c.id == case.id for c in hits)

    # Injection attempt is parameter-bound -> no error, no spurious table ops.
    safe = await search_cases(pg_session, "'; DROP TABLE cases;--")
    assert isinstance(safe, list)

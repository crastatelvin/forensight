"""Shared pytest fixtures for Phase 1 auth tests.

Builds the app against an in-memory SQLite DB (aiosqlite) so auth flows can be
exercised without Postgres, while still using the real ORM models + routes.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from app.auth import models as auth_models
from app.auth.ratelimit import InMemoryRateLimiter
from app.auth.repository import create_user
from app.auth.roles import Role
from app.config import Settings, get_settings
from app.db import Base, get_session
from app.domain import models as domain_models
from app.main import create_app
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "secret_key": "test-secret-key-not-real-0123456789",
        "environment": "development",
        "cookie_secure": False,
        "rate_limit_auth_max": 5,
        "rate_limit_auth_window": 60,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture
async def sessionmaker_fixture() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Register auth + domain models, then create all tables on this engine.
        _ = auth_models.User
        _ = domain_models.Case
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield maker
    await engine.dispose()


@pytest.fixture
async def app(
    settings: Settings, sessionmaker_fixture: async_sessionmaker[AsyncSession]
) -> FastAPI:
    application = create_app(settings)
    application.state.rate_limiter = InMemoryRateLimiter()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker_fixture() as session:
            yield session

    application.dependency_overrides[get_session] = _override_get_session
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def seed_users(
    sessionmaker_fixture: async_sessionmaker[AsyncSession],
) -> dict[str, dict[str, str]]:
    """Create one user per role. Returns creds keyed by role name."""
    creds = {
        Role.ADMIN.value: {"email": "admin@t.io", "password": "AdminPassw0rd!!"},
        Role.OFFICER.value: {"email": "officer@t.io", "password": "OfficerPassw0rd!!"},
        Role.EXPERT.value: {"email": "expert@t.io", "password": "ExpertPassw0rd!!"},
        Role.REVIEWER.value: {"email": "reviewer@t.io", "password": "ReviewPassw0rd!!"},
    }
    async with sessionmaker_fixture() as session:
        for role_value, c in creds.items():
            await create_user(
                session, email=c["email"], password=c["password"], role=Role(role_value)
            )
        await session.commit()
    return creds


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@t.io"


@pytest.fixture
def make_email() -> Iterator[object]:
    yield unique_email

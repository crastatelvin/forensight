"""Phase 1 seed-script test (T1.7): no default/hardcoded credentials."""
from __future__ import annotations

import pytest
from app.auth.repository import get_user_by_email
from app.auth.roles import Role
from app.config import Settings


async def test_seed_requires_env_password(monkeypatch: pytest.MonkeyPatch) -> None:
    # T1.7: without SEED_ADMIN_* the seed refuses (returns non-zero, no user).
    import app.seed as seed_mod

    monkeypatch.setattr(
        seed_mod, "get_settings", lambda: Settings(secret_key="x" * 20)
    )
    rc = await seed_mod.seed_admin()
    assert rc == 2  # refused; no default credentials


async def test_seed_creates_admin_with_env(
    monkeypatch: pytest.MonkeyPatch, sessionmaker_fixture
) -> None:
    import app.seed as seed_mod

    settings = Settings(
        secret_key="x" * 20,
        seed_admin_email="root@t.io",
        seed_admin_password="StrongSeedPass12!",
    )
    monkeypatch.setattr(seed_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(seed_mod, "SessionLocal", sessionmaker_fixture)

    rc = await seed_mod.seed_admin()
    assert rc == 0

    async with sessionmaker_fixture() as session:
        user = await get_user_by_email(session, "root@t.io")
        assert user is not None
        assert user.role == Role.ADMIN.value
        assert user.hashed_password.startswith("$argon2id$")

    # Idempotent: running again does nothing and still succeeds.
    rc2 = await seed_mod.seed_admin()
    assert rc2 == 0

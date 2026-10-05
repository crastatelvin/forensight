"""Data-access helpers for users and refresh tokens (Phase 1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RefreshToken, User
from app.auth.roles import Role
from app.auth.security import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.__table__.c.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def create_user(session: AsyncSession, *, email: str, password: str, role: Role) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        role=role.value,
        is_active=True,
        is_superuser=(role == Role.ADMIN),
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def issue_refresh_token(
    session: AsyncSession, *, user_id: uuid.UUID, ttl_seconds: int
) -> str:
    raw = generate_refresh_token()
    record = RefreshToken(
        user_id=user_id,
        token_hash=hash_refresh_token(raw),
        revoked=False,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    session.add(record)
    await session.flush()
    return raw


async def get_refresh_record(session: AsyncSession, raw_token: str) -> RefreshToken | None:
    token_hash = hash_refresh_token(raw_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    return result.scalar_one_or_none()


async def revoke_all_user_refresh_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
    )
    for record in result.scalars().all():
        record.revoked = True

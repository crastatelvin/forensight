"""Auth API routes (Phase 1): register (admin), login, refresh, logout, me."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import CurrentUser, get_current_user, require_roles
from app.auth.repository import (
    create_user,
    get_refresh_record,
    get_user_by_email,
    get_user_by_id,
    issue_refresh_token,
    revoke_all_user_refresh_tokens,
)
from app.auth.roles import Role
from app.auth.schemas import (
    LoginRequest,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserRead,
)
from app.auth.security import hash_password, verify_password
from app.auth.service import enforce_auth_rate_limit, issue_access
from app.config import Settings, get_settings
from app.db import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

# Precomputed dummy Argon2id hash for constant-time login on unknown users
# (never matches a real password); mitigates user-enumeration timing (T1.1).
_DUMMY_HASH = hash_password("dummy-password-for-timing-only-xyz")


@router.post("/register", response_model=UserRead, status_code=201)
async def register(
    payload: UserCreate,
    _admin: Annotated[CurrentUser, Depends(require_roles(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserRead:
    if await get_user_by_email(session, payload.email) is not None:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = await create_user(
        session, email=payload.email, password=payload.password, role=payload.role
    )
    await session.commit()
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    await enforce_auth_rate_limit(request, settings)
    user = await get_user_by_email(session, payload.email)
    if user is None:
        verify_password(payload.password, _DUMMY_HASH)  # keep timing similar
        raise HTTPException(status_code=401, detail="Invalid credentials")

    ok, new_hash = verify_password(payload.password, user.hashed_password)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if new_hash is not None:
        user.hashed_password = new_hash
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    access = issue_access(str(user.id), user.role, settings)
    refresh_token = await issue_refresh_token(
        session, user_id=user.id, ttl_seconds=settings.refresh_token_ttl
    )
    await session.commit()
    return TokenPair(access_token=access, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    await enforce_auth_rate_limit(request, settings)
    record = await get_refresh_record(session, payload.refresh_token)
    if record is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if record.revoked:
        # Reuse of a rotated/revoked token -> revoke the whole family (T1.2).
        await revoke_all_user_refresh_tokens(session, record.user_id)
        await session.commit()
        raise HTTPException(status_code=401, detail="Refresh token reuse detected")

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise HTTPException(status_code=401, detail="Refresh token expired")

    record.revoked = True  # rotate
    user = await get_user_by_id(session, record.user_id)
    if user is None or not user.is_active:
        await session.commit()
        raise HTTPException(status_code=401, detail="User not available")

    access = issue_access(str(user.id), user.role, settings)
    new_refresh = await issue_refresh_token(
        session, user_id=user.id, ttl_seconds=settings.refresh_token_ttl
    )
    await session.commit()
    return TokenPair(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=204)
async def logout(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> Response:
    await revoke_all_user_refresh_tokens(session, uuid.UUID(user.id))
    await session.commit()
    response.delete_cookie("access_token")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserRead)
async def me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserRead:
    db_user = await get_user_by_id(session, uuid.UUID(user.id))
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserRead.model_validate(db_user)


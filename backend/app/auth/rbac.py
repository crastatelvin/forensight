"""RBAC dependencies — enforced centrally, not copy-pasted per route (T1.3).

`get_current_user` validates the bearer JWT. `require_roles(...)` builds a
dependency that authorizes a set of roles. Reviewer is read-only: it never
appears in mutating-route role sets.
"""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.roles import Role
from app.auth.security import decode_access_token
from app.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(self, user_id: str, role: Role) -> None:
        self.id = user_id
        self.role = role


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentUser:
    # Accept bearer header; fall back to access-token cookie for cookie flows.
    token: str | None = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    else:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(
            token, secret_key=settings.secret_key, algorithm=settings.jwt_algorithm
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        role = Role(payload["role"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims"
        ) from exc

    return CurrentUser(user_id=str(payload["sub"]), role=role)


def require_roles(
    *allowed: Role,
) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    allowed_set = frozenset(allowed)

    async def _dep(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )
        return user

    return _dep

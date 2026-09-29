"""Password hashing (Argon2id) and JWT token handling (Phase 1).

- Passwords: Argon2id via pwdlib (the hasher fastapi-users 14+ standardises on).
  No plaintext is ever stored; verification is constant-time (T1.1).
- JWT: short-lived access tokens + opaque refresh tokens. Access tokens are
  signed with the app SECRET_KEY; refresh tokens are random and stored hashed
  server-side for rotation/revocation (T1.2).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

# Argon2id-only hasher.
_password_hash = PasswordHash((Argon2Hasher(),))


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> tuple[bool, str | None]:
    """Verify a password. Returns (ok, new_hash_if_rehash_needed)."""
    return _password_hash.verify_and_update(password, hashed)


# --- Access tokens (signed JWT) ---

def create_access_token(
    *, subject: str, role: str, secret_key: str, algorithm: str, ttl_seconds: int
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, secret_key, algorithm=algorithm)


def decode_access_token(
    token: str, *, secret_key: str, algorithm: str
) -> dict[str, Any]:
    """Decode + validate a JWT. Raises jwt.PyJWTError on any problem (T1.2)."""
    payload: dict[str, Any] = jwt.decode(token, secret_key, algorithms=[algorithm])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return payload


# --- Refresh tokens (opaque, stored hashed) ---

def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Deterministic hash for storage/lookup (token itself is high-entropy)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

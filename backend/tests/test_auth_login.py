"""Phase 1 auth tests: hashing, login, JWT/refresh rotation (T1.1, T1.2)."""

from __future__ import annotations

from app.auth.security import (
    decode_access_token,
    hash_password,
    verify_password,
)
from httpx import AsyncClient


def test_argon2id_hash_and_verify() -> None:
    # T1.1: Argon2id hash stored; verifies; wrong password rejected.
    hashed = hash_password("CorrectHorse12!")
    assert hashed.startswith("$argon2id$")
    assert "CorrectHorse12!" not in hashed  # no plaintext
    ok, _ = verify_password("CorrectHorse12!", hashed)
    assert ok is True
    bad, _ = verify_password("wrong-password", hashed)
    assert bad is False


async def test_login_success_and_wrong_password(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    creds = seed_users["officer"]
    resp = await client.post("/auth/login", json=creds)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]

    bad = await client.post(
        "/auth/login", json={"email": creds["email"], "password": "nope-nope-nope"}
    )
    assert bad.status_code == 401


async def test_login_unknown_user_rejected(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    resp = await client.post(
        "/auth/login", json={"email": "ghost@t.io", "password": "whatever12345"}
    )
    assert resp.status_code == 401


async def test_access_token_claims(
    client: AsyncClient, seed_users: dict[str, dict[str, str]], settings
) -> None:
    creds = seed_users["expert"]
    resp = await client.post("/auth/login", json=creds)
    token = resp.json()["access_token"]
    payload = decode_access_token(
        token, secret_key=settings.secret_key, algorithm=settings.jwt_algorithm
    )
    assert payload["role"] == "expert"
    assert payload["type"] == "access"


async def test_forged_token_rejected(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # T1.2: forged/invalid JWT rejected on a protected route.
    resp = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.real.token"})
    assert resp.status_code == 401


async def test_refresh_rotation_and_reuse_revocation(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # T1.2: refresh rotates; reusing an old refresh token is rejected + revokes family.
    login = await client.post("/auth/login", json=seed_users["officer"])
    first_refresh = login.json()["refresh_token"]

    r1 = await client.post("/auth/refresh", json={"refresh_token": first_refresh})
    assert r1.status_code == 200
    new_refresh = r1.json()["refresh_token"]
    assert new_refresh != first_refresh

    # Reusing the now-rotated first token must fail (reuse detected).
    reuse = await client.post("/auth/refresh", json={"refresh_token": first_refresh})
    assert reuse.status_code == 401

    # And the family is revoked: the rotated token no longer works either.
    after = await client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert after.status_code == 401


async def test_me_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/auth/me")
    assert resp.status_code == 401

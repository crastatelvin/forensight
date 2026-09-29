"""Phase 1 RBAC + rate-limit + headers + CSRF tests (T1.3–T1.7)."""
from __future__ import annotations

import pytest
from app.auth.roles import Role
from httpx import AsyncClient


async def _login(client: AsyncClient, creds: dict[str, str]) -> str:
    resp = await client.post("/auth/login", json=creds)
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.mark.parametrize(
    "role_value,expected",
    [
        (Role.ADMIN.value, 201),  # only admin may register users
        (Role.OFFICER.value, 403),
        (Role.EXPERT.value, 403),
        (Role.REVIEWER.value, 403),
    ],
)
async def test_rbac_register_matrix(
    client: AsyncClient,
    seed_users: dict[str, dict[str, str]],
    role_value: str,
    expected: int,
) -> None:
    # T1.3: only Admin can hit the admin-only register route.
    token = await _login(client, seed_users[role_value])
    resp = await client.post(
        "/auth/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": f"new-{role_value}@t.io",
            "password": "BrandNewPass12!",
            "role": "reviewer",
        },
    )
    assert resp.status_code == expected


async def test_rate_limit_returns_429(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # T1.4: after threshold (5) auth attempts, the limiter returns 429.
    creds = {"email": "officer@t.io", "password": "wrong-password-xx"}
    statuses = []
    for _ in range(7):
        r = await client.post("/auth/login", json=creds)
        statuses.append(r.status_code)
    assert 429 in statuses
    # First few are 401 (bad creds), later ones flip to 429.
    assert statuses.count(401) <= 5


async def test_security_headers_present(client: AsyncClient) -> None:
    # T1.6: headers on every response.
    resp = await client.get("/healthz")
    h = resp.headers
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in h
    assert h.get("Referrer-Policy") == "no-referrer"


async def test_csrf_required_for_cookie_mutation(client: AsyncClient) -> None:
    # T1.5: a cookie-authenticated mutation without CSRF header -> 403.
    resp = await client.post(
        "/auth/logout",
        cookies={"access_token": "dummy", "csrf_token": "abc123"},
        # No X-CSRF-Token header -> must be rejected by CSRF middleware.
    )
    assert resp.status_code == 403


async def test_bearer_mutation_exempt_from_csrf(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # T1.5: bearer (header) auth is not subject to CSRF; logout succeeds.
    token = await _login(client, seed_users["reviewer"])
    resp = await client.post(
        "/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 204

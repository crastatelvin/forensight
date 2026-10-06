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
    resp = await client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204


# --- unique-constraint violations are the caller's fault (T0-class) ---------


async def test_duplicate_case_number_is_409_not_500(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """A unique-constraint violation is a client mistake, not a server fault.

    It returned **500** before, which blamed the server for the caller's duplicate
    and leaked a stack trace through the 500 body. Found by creating the same case
    number twice against the running stack — the suite generates a fresh number per
    test, so nothing ever collided.
    """
    officer = {"Authorization": f"Bearer {await _login(client, seed_users['officer'])}"}
    body = {"number": "DUP-409", "title": "First"}

    first = await client.post("/cases", headers=officer, json=body)
    assert first.status_code == 201, first.text

    second = await client.post("/cases", headers=officer, json=body)
    assert second.status_code == 409, second.text
    assert "Traceback" not in second.text
    # No driver internals leaked to the caller. This is the part that matters on
    # SQLite, which does not expose `constraint_name` at all — the handler falls
    # back to a generic phrase rather than echoing an exception class.
    assert "asyncpg" not in second.text.lower()
    assert "IntegrityError" not in second.text
    assert "UniqueViolation" not in second.text


def test_constraint_detail_prefers_the_driver_constraint_name() -> None:
    """On asyncpg the caller learns *which* constraint collided.

    Only asyncpg exposes `.constraint_name`; SQLite does not, and neither does
    SQLAlchemy itself. The handler therefore has two cases, and both are tested —
    the SQLite one above, this one here.
    """
    from app.main import _constraint_detail
    from sqlalchemy.exc import IntegrityError

    # asyncpg raises its own error; SQLAlchemy wraps it, so the handler has to
    # unwrap. Both shapes are asserted.
    class _AsyncpgOrig(Exception):
        constraint_name = "ix_cases_number"

    class _SqlalchemyWrapper(Exception):
        orig = _AsyncpgOrig("duplicate key")

    named = IntegrityError("stmt", {}, _AsyncpgOrig("duplicate key"))
    assert _constraint_detail(named) == "ix_cases_number"

    wrapped = IntegrityError("stmt", {}, _SqlalchemyWrapper("duplicate key"))
    assert _constraint_detail(wrapped) == "ix_cases_number"

    anonymous = IntegrityError("stmt", {}, Exception("duplicate key"))
    assert _constraint_detail(anonymous) == "a unique constraint"


# --- rate limiting: reads and writes are different budgets ------------------


async def test_reading_a_case_page_does_not_exhaust_the_write_budget(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """A case page is several reads; it must not be throttled like a write flood.

    The whole cases router was limited by `mutation_rate_limit` at 60/min, so
    ordinary navigation returned 429: one case page renders seven reads in
    parallel, which is the UI's normal behaviour, not an attack. Reads now have
    their own, larger budget and writes keep the tight one — sharing a bucket
    meant a page full of reads spent the write allowance, and the only way to
    "fix" that by lowering the number would have throttled real writes.
    """
    officer = {"Authorization": f"Bearer {await _login(client, seed_users['officer'])}"}
    case_id = (
        await client.post("/cases", headers=officer, json={"number": "RL-1", "title": "Reads"})
    ).json()["id"]

    # Well past the write budget of 60/min. Every one of these must succeed.
    reads: list[int] = []
    for _ in range(70):
        for path in ("", "/evidence", "/findings", "/timeline"):
            resp = await client.get(f"/cases/{case_id}{path}", headers=officer)
            reads.append(resp.status_code)
            assert resp.status_code == 200, (
                f"{path} returned {resp.status_code} after {len(reads)} reads: "
                f"{resp.text[:120]}"
            )
    assert len(reads) == 280


async def test_writes_are_still_capped(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """Splitting the buckets must not have removed the write ceiling.

    The read budget went up by 10x, so the thing that would catch this is a test
    asserting writes are *still* limited — otherwise the fix is just "raise the
    numbers", which is not a fix.
    """
    officer = {"Authorization": f"Bearer {await _login(client, seed_users['officer'])}"}
    statuses: list[int] = []
    for i in range(70):
        resp = await client.post(
            "/cases",
            headers=officer,
            json={"number": f"RLW-{i}", "title": "Write flood"},
        )
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert 429 in statuses, f"writes were never limited: {set(statuses)}"
    assert statuses[0] == 201, "the first write should succeed"

"""Phase 3 access-control tests (T3.7): per-case ownership/assignment scoping.

Verifies:
- Officer cannot read/modify a case owned by another officer (404 non-disclosure).
- Expert can only access cases assigned to them.
- Reviewer can read any case but cannot write.
- Admin can read/write any case.
- Assignment to a non-existent user is rejected (422).
- Search results are scoped to the caller.
"""
from __future__ import annotations

import uuid

from httpx import AsyncClient


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    r = await client.post("/auth/login", json=creds)
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _make_case(
    client: AsyncClient, headers: dict[str, str], number: str
) -> str:
    r = await client.post(
        "/cases", headers=headers, json={"number": number, "title": "T"}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_officer_cannot_access_other_officers_case(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # Two distinct officers: register a second officer via admin.
    admin = await _auth(client, seed_users["admin"])
    second = {"email": "officer2@t.io", "password": "Officer2Pass12!"}
    reg = await client.post(
        "/auth/register", headers=admin,
        json={**second, "role": "officer"},
    )
    assert reg.status_code == 201

    officer1 = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, officer1, "OWN-1")

    officer2 = await _auth(client, second)
    # Officer2 does not own and is not assigned -> 404 (non-disclosure).
    got = await client.get(f"/cases/{case_id}", headers=officer2)
    assert got.status_code == 404
    # And cannot add findings.
    f = await client.post(
        f"/cases/{case_id}/findings", headers=officer2,
        json={"title": "x", "body": ""},
    )
    assert f.status_code == 404


async def test_admin_can_access_any_case(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    officer = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, officer, "ADM-1")
    admin = await _auth(client, seed_users["admin"])
    got = await client.get(f"/cases/{case_id}", headers=admin)
    assert got.status_code == 200


async def test_reviewer_can_read_but_not_write(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    officer = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, officer, "REV-1")
    reviewer = await _auth(client, seed_users["reviewer"])
    # Reviewer reads any case.
    assert (await client.get(f"/cases/{case_id}", headers=reviewer)).status_code == 200
    # Reviewer cannot create findings (RBAC: 403, reviewer not a mutator).
    f = await client.post(
        f"/cases/{case_id}/findings", headers=reviewer,
        json={"title": "x", "body": ""},
    )
    assert f.status_code == 403


async def test_expert_only_sees_assigned_case(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    officer = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, officer, "EXP-1")
    expert = await _auth(client, seed_users["expert"])

    # Before assignment, expert cannot see it.
    assert (await client.get(f"/cases/{case_id}", headers=expert)).status_code == 404

    # Officer assigns the expert.
    assign = await client.post(
        f"/cases/{case_id}/assign", headers=officer,
        json={"assigned_to": seed_users["expert"]["id"]},
    )
    assert assign.status_code == 200

    # Now the expert can read it.
    assert (await client.get(f"/cases/{case_id}", headers=expert)).status_code == 200


async def test_assign_to_nonexistent_user_rejected(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    officer = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, officer, "ASG-404")
    bad = await client.post(
        f"/cases/{case_id}/assign", headers=officer,
        json={"assigned_to": str(uuid.uuid4())},
    )
    assert bad.status_code == 422


async def test_search_is_scoped_to_caller(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # Officer1 creates a case with a unique marker.
    officer1 = await _auth(client, seed_users["officer"])
    await client.post(
        "/cases", headers=officer1,
        json={"number": "SCOPE-1", "title": "Zephyrmarker Case"},
    )

    # A second officer must not find it via search.
    admin = await _auth(client, seed_users["admin"])
    second = {"email": "officer3@t.io", "password": "Officer3Pass12!"}
    await client.post(
        "/auth/register", headers=admin, json={**second, "role": "officer"}
    )
    officer3 = await _auth(client, second)
    res = await client.get(
        "/cases/search/query", headers=officer3, params={"q": "Zephyrmarker"}
    )
    assert res.status_code == 200
    assert all("Zephyrmarker" not in c["title"] for c in res.json())

    # Admin searches organisation-wide and finds it.
    admin_res = await client.get(
        "/cases/search/query", headers=admin, params={"q": "Zephyrmarker"}
    )
    assert any("Zephyrmarker" in c["title"] for c in admin_res.json())

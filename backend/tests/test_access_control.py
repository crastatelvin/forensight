"""Object-level authorization tests (P2.B5, T3.1, T3.7).

The security property under test: knowing an id is not authorization.

Policy under test:
- ADMIN and REVIEWER may *read* every case — oversight is the point of the
  role, and RBAC separately keeps reviewer read-only.
- OFFICER/EXPERT may read and write only cases they own or are assigned to.
  This is the actual isolation boundary: two officers working different
  investigations must not read each other's evidence.
- A case the caller cannot see is reported as 404, not 403, so an id cannot be
  probed for existence — and a write discloses no more than a read.

Also covers: assignment grants visibility, assignment to a non-existent user is
rejected, and search/listing are scoped so they cannot be used to bypass case
access.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.fixture
def fake_store():
    from app.domain import router as domain_router
    from app.domain.antivirus import NoopScanner
    from app.domain.service import IngestService

    from tests.test_domain_integration import FakeStore

    service = IngestService(FakeStore(), NoopScanner())
    original = domain_router._ingest_service
    domain_router._ingest_service = lambda settings: service
    try:
        yield service
    finally:
        domain_router._ingest_service = original


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    r = await client.post("/auth/login", json=creds)
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _make_case(client: AsyncClient, headers: dict[str, str], number: str) -> str:
    r = await client.post("/cases", headers=headers, json={"number": number, "title": "T"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_officer_cannot_read_another_users_case_or_evidence(
    client: AsyncClient, seed_users: dict[str, dict[str, str]], fake_store
) -> None:
    """The real boundary: a case the caller neither owns nor is assigned to."""
    officer = await _auth(client, seed_users["officer"])
    expert = await _auth(client, seed_users["expert"])
    case_id = await _make_case(client, officer, "AC-2")

    up = await client.post(
        f"/cases/{case_id}/evidence",
        headers=officer,
        files={
            "file": (
                "secret.png",
                __import__("base64").b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
                ),
                "application/octet-stream",
            )
        },
    )
    assert up.status_code == 201
    eid = up.json()["id"]
    png = __import__("base64").b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    assert (await client.get(f"/cases/{case_id}", headers=expert)).status_code == 404
    assert (await client.get(f"/cases/{case_id}/evidence", headers=expert)).status_code == 404
    assert (await client.get(f"/cases/{case_id}/findings", headers=expert)).status_code == 404

    denied = await client.get(f"/cases/evidence/{eid}/download", headers=expert)
    assert denied.status_code == 404
    assert denied.content != png  # no bytes in the body either

    # Custody log is equally protected: it names actors and hashes.
    assert (await client.get(f"/cases/evidence/{eid}/custody", headers=expert)).status_code == 404

    # Export would hand over the whole case record.
    assert (
        await client.get(f"/cases/{case_id}/export", headers=expert, params={"fmt": "json"})
    ).status_code == 404


async def test_second_officer_cannot_write_to_a_foreign_case(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """Two distinct officers, not just officer-vs-expert: isolation must hold
    between peers working different investigations."""
    admin = await _auth(client, seed_users["admin"])
    second = {"email": "officer2@example.com", "password": "Officer2Pass12!"}
    reg = await client.post("/auth/register", headers=admin, json={**second, "role": "officer"})
    assert reg.status_code == 201

    officer1 = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, officer1, "OWN-1")
    officer2 = await _auth(client, second)

    # Not owner, not assigned -> 404 on read and on every write. A write must
    # not disclose more than a read does.
    assert (await client.get(f"/cases/{case_id}", headers=officer2)).status_code == 404
    assert (
        await client.post(f"/cases/{case_id}/findings", headers=officer2, json={"title": "x"})
    ).status_code == 404
    assert (
        await client.patch(f"/cases/{case_id}", headers=officer2, json={"title": "hijacked"})
    ).status_code == 404
    assert (
        await client.post(f"/cases/{case_id}/transition", headers=officer2, json={"target": "open"})
    ).status_code == 404
    assert (
        await client.get(f"/cases/{case_id}/export", headers=officer2, params={"fmt": "json"})
    ).status_code == 404


async def test_admin_can_access_any_case(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    officer = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, officer, "ADM-1")
    admin = await _auth(client, seed_users["admin"])
    assert (await client.get(f"/cases/{case_id}", headers=admin)).status_code == 200


async def test_reviewer_reads_every_case_but_writes_none(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """Oversight means global *read*; RBAC is what keeps reviewer read-only."""
    officer = await _auth(client, seed_users["officer"])
    reviewer = await _auth(client, seed_users["reviewer"])
    case_id = await _make_case(client, officer, "REV-1")

    assert (await client.get(f"/cases/{case_id}", headers=reviewer)).status_code == 200
    assert (await client.get(f"/cases/{case_id}/findings", headers=reviewer)).status_code == 200
    assert (
        await client.get(f"/cases/{case_id}/export", headers=reviewer, params={"fmt": "json"})
    ).status_code == 200

    # ...and every write is refused.
    assert (
        await client.post(f"/cases/{case_id}/findings", headers=reviewer, json={"title": "x"})
    ).status_code == 403
    assert (
        await client.post(f"/cases/{case_id}/transition", headers=reviewer, json={"target": "open"})
    ).status_code == 403
    assert (
        await client.patch(f"/cases/{case_id}", headers=reviewer, json={"title": "x"})
    ).status_code == 403


async def test_assignment_grants_visibility(
    client: AsyncClient, seed_users: dict[str, dict[str, str]], sessionmaker_fixture
) -> None:
    """Assigning a case must actually let the assignee see it — otherwise
    scoping would make collaboration impossible."""
    officer = await _auth(client, seed_users["officer"])
    expert = await _auth(client, seed_users["expert"])
    case_id = await _make_case(client, officer, "EXP-1")

    assert (await client.get(f"/cases/{case_id}", headers=expert)).status_code == 404

    assign = await client.post(
        f"/cases/{case_id}/assign",
        headers=officer,
        json={"assigned_to": seed_users["expert"]["id"]},
    )
    assert assign.status_code == 200
    assert assign.json()["assigned_to"] == seed_users["expert"]["id"]

    assert (await client.get(f"/cases/{case_id}", headers=expert)).status_code == 200


async def test_expert_sees_only_their_own_and_assigned_cases(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """Experts are scoped like officers: own cases plus assigned ones.

    Both roles are mutators, so both can open a case and own it. Scoping experts
    to assigned-only would lock them out of the cases they just created.
    """
    officer = await _auth(client, seed_users["officer"])
    expert = await _auth(client, seed_users["expert"])

    own = await _make_case(client, expert, "EXP-OWN")
    assert (await client.get(f"/cases/{own}", headers=expert)).status_code == 200

    theirs = await _make_case(client, officer, "EXP-THEIRS")
    assert (await client.get(f"/cases/{theirs}", headers=expert)).status_code == 404


async def test_assign_to_nonexistent_user_is_rejected(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """A dangling assignee would silently break the notification and the grant."""
    officer = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, officer, "ASG-404")
    bad = await client.post(
        f"/cases/{case_id}/assign",
        headers=officer,
        json={"assigned_to": str(uuid.uuid4())},
    )
    assert bad.status_code == 422


async def test_search_does_not_leak_across_case_scope(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """Search must be scoped too, or it is a universal read oracle."""
    officer = await _auth(client, seed_users["officer"])
    expert = await _auth(client, seed_users["expert"])
    marker = "Zzyzxmarker"
    await client.post("/cases", headers=officer, json={"number": "SCOPE-1", "title": marker})

    mine = await client.get("/cases/search/query", headers=officer, params={"q": marker})
    assert any(marker in c["title"] for c in mine.json())

    # An unassigned searcher must not retrieve it by title.
    theirs = await client.get("/cases/search/query", headers=expert, params={"q": marker})
    assert all(marker not in c["title"] for c in theirs.json())

    # A reviewer, by contrast, sees all of it — oversight again.
    reviewer = await _auth(client, seed_users["reviewer"])
    audit = await client.get("/cases/search/query", headers=reviewer, params={"q": marker})
    assert any(marker in c["title"] for c in audit.json())


async def test_case_list_is_scoped(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    officer = await _auth(client, seed_users["officer"])
    expert = await _auth(client, seed_users["expert"])
    await client.post("/cases", headers=officer, json={"number": "AC-7", "title": "T"})

    mine = await client.get("/cases", headers=officer)
    assert any(c["number"] == "AC-7" for c in mine.json())

    theirs = await client.get("/cases", headers=expert)
    assert all(c["number"] != "AC-7" for c in theirs.json())


async def test_unknown_case_is_404_not_empty_list(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """Clients must be able to tell 'no such case' from 'no evidence'."""
    officer = await _auth(client, seed_users["officer"])
    missing = str(uuid.uuid4())
    for path in (
        f"/cases/{missing}/evidence",
        f"/cases/{missing}/findings",
        f"/cases/{missing}/entities",
        f"/cases/{missing}/timeline",
    ):
        assert (await client.get(path, headers=officer)).status_code == 404


async def test_case_reports_its_legal_transitions(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """The workflow is served, not reimplemented by clients.

    The state machine lives in one place (`CASE_TRANSITIONS`). A UI holding its own
    copy will offer a move the API then rejects with 409, and a reviewer reads that
    as a broken case rather than a stale button -- so `CaseRead` carries the legal
    moves and every serialisation site fills them in.

    `case_to_read` exists because there are eight call sites: one that forgets the
    derived field returns a case claiming there are no moves, which reads as
    "closed" when it is not.
    """
    headers = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, headers, "TX-1")

    body = (await client.get(f"/cases/{case_id}", headers=headers)).json()

    # Intake can open or archive. An empty list would make the workflow look stuck.
    assert "open" in body["allowed_transitions"], body
    assert "archived" in body["allowed_transitions"], body

    moved = await client.post(
        f"/cases/{case_id}/transition", headers=headers, json={"target": "open"}
    )
    assert moved.status_code == 200, moved.text
    assert "investigating" in moved.json()["allowed_transitions"]

    # The list endpoint must agree with the detail endpoint. Re-read the detail
    # first: `body` is the pre-transition state, so comparing against it would be
    # comparing a stale answer to a fresh one and always fail.
    current = (await client.get(f"/cases/{case_id}", headers=headers)).json()
    listing = await client.get("/cases?limit=50", headers=headers)
    match = [c for c in listing.json() if c["id"] == case_id]
    assert match, listing.text
    assert match[0]["allowed_transitions"] == current["allowed_transitions"], (
        "the list and detail endpoints disagree about the workflow, which is how a "
        "button ends up offering a move the API will reject"
    )


async def test_archived_case_offers_no_further_moves(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    """`archived` is terminal, and that has to be visible as an empty list.

    An absent field and an empty list mean different things: one is a bug, the
    other is a completed case. Returning `[]` is what makes the difference legible
    to a client.
    """
    headers = await _auth(client, seed_users["officer"])
    case_id = await _make_case(client, headers, "TX-ARCHIVE")

    for target in ("open", "archived"):
        await client.post(f"/cases/{case_id}/transition", headers=headers, json={"target": target})

    body = (await client.get(f"/cases/{case_id}", headers=headers)).json()
    assert body["status"] == "archived"
    assert body["allowed_transitions"] == [], body

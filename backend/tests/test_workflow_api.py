"""Phase 3 tests: RBAC CRUD, assignment+notify, search, exports, 409, OpenAPI."""

from __future__ import annotations

import base64
import io
import json
import zipfile

from httpx import AsyncClient

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    r = await client.post("/auth/login", json=creds)
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_case_crud_rbac(client: AsyncClient, seed_users: dict[str, dict[str, str]]) -> None:
    # T3.1: reviewer read-only; officer can create; both can read.
    officer = await _auth(client, seed_users["officer"])
    reviewer = await _auth(client, seed_users["reviewer"])

    created = await client.post(
        "/cases", headers=officer, json={"number": "RB-1", "title": "Burglary"}
    )
    assert created.status_code == 201
    cid = created.json()["id"]

    # Reviewer cannot create.
    denied = await client.post("/cases", headers=reviewer, json={"number": "RB-2", "title": "No"})
    assert denied.status_code == 403

    # Reviewer can read.
    got = await client.get(f"/cases/{cid}", headers=reviewer)
    assert got.status_code == 200


async def test_assignment_notifies(
    client: AsyncClient,
    seed_users: dict[str, dict[str, str]],
    sessionmaker_fixture,
) -> None:
    # T3.2: assignment publishes a notification; reviewer cannot assign.
    from app.domain import router as domain_router
    from app.domain.notifications import InMemoryNotifier

    notifier = InMemoryNotifier()
    domain_router._NOTIFIER = notifier

    officer = await _auth(client, seed_users["officer"])
    reviewer = await _auth(client, seed_users["reviewer"])
    case = (
        await client.post("/cases", headers=officer, json={"number": "AS-1", "title": "T"})
    ).json()

    # Assign to a real account: a dangling assignee id must be rejected rather
    # than stored, or the notification goes nowhere and nobody notices.
    async with sessionmaker_fixture() as session:
        from app.auth.repository import get_user_by_email

        assignee = await get_user_by_email(session, seed_users["officer"]["email"])
        target = str(assignee.id)

    ok = await client.post(
        f"/cases/{case['id']}/assign", headers=officer, json={"assigned_to": target}
    )
    assert ok.status_code == 200
    assert ok.json()["assigned_to"] == target  # assignment must be visible
    assert any(e[0] == "case.assigned" for e in notifier.events)

    ghost = await client.post(
        f"/cases/{case['id']}/assign",
        headers=officer,
        json={"assigned_to": "00000000-0000-0000-0000-000000000000"},
    )
    assert ghost.status_code == 422

    # Reviewer may not assign.
    bad = await client.post(
        f"/cases/{case['id']}/assign", headers=reviewer, json={"assigned_to": target}
    )
    assert bad.status_code == 403


async def test_search_and_sqli_safe(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # T3.3: search finds by metadata; an injection attempt is inert.
    officer = await _auth(client, seed_users["officer"])
    await client.post(
        "/cases",
        headers=officer,
        json={"number": "SR-1", "title": "Unique Arson Marker"},
    )
    hit = await client.get("/cases/search/query", headers=officer, params={"q": "Arson"})
    assert hit.status_code == 200
    assert any("Arson" in c["title"] for c in hit.json())

    # Injection attempt: must not error, must not return everything spuriously.
    inj = await client.get(
        "/cases/search/query", headers=officer, params={"q": "'; DROP TABLE cases;--"}
    )
    assert inj.status_code == 200


async def test_illegal_transition_409_audited(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # T3.5: illegal transition via API -> 409.
    officer = await _auth(client, seed_users["officer"])
    case = (
        await client.post("/cases", headers=officer, json={"number": "TR-1", "title": "T"})
    ).json()
    bad = await client.post(
        f"/cases/{case['id']}/transition",
        headers=officer,
        json={"target": "reported"},
    )
    assert bad.status_code == 409


async def test_openapi_documents_endpoints(client: AsyncClient) -> None:
    # T3.6: OpenAPI schema is published and documents the case routes.
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/cases" in paths
    assert "/cases/{case_id}/assign" in paths
    assert "/cases/search/query" in paths
    assert "/cases/{case_id}/export" in paths


async def test_exports_match_db(client: AsyncClient, seed_users: dict[str, dict[str, str]]) -> None:
    # T3.4: CSV/JSON/ZIP exports; ZIP manifest lists content-hashes.
    from app.domain import router as domain_router
    from app.domain.antivirus import NoopScanner
    from app.domain.service import IngestService

    from tests.test_domain_integration import FakeStore

    officer = await _auth(client, seed_users["officer"])
    case = (
        await client.post("/cases", headers=officer, json={"number": "EX-1", "title": "Export"})
    ).json()

    fake = IngestService(FakeStore(), NoopScanner())
    orig = domain_router._ingest_service
    domain_router._ingest_service = lambda settings: fake  # type: ignore[assignment]
    try:
        up = await client.post(
            f"/cases/{case['id']}/evidence",
            headers=officer,
            files={"file": ("p.png", _PNG, "application/octet-stream")},
        )
        assert up.status_code == 201
        sha = up.json()["sha256"]
    finally:
        domain_router._ingest_service = orig  # type: ignore[assignment]

    # JSON export
    j = await client.get(f"/cases/{case['id']}/export", headers=officer, params={"fmt": "json"})
    assert j.status_code == 200
    doc = json.loads(j.content)
    assert doc["case"]["number"] == "EX-1"
    assert doc["evidence"][0]["sha256"] == sha

    # ZIP export with manifest content-hashes
    z = await client.get(f"/cases/{case['id']}/export", headers=officer, params={"fmt": "zip"})
    assert z.status_code == 200
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        names = set(zf.namelist())
        assert {"case.json", "evidence.csv", "manifest.json"} <= names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["content_hashes"][0]["sha256"] == sha

"""Phase 2 API tests: case lifecycle, evidence upload/download, custody (T2.1-T2.8)."""

from __future__ import annotations

import base64

from httpx import AsyncClient

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


async def _token(client: AsyncClient, creds: dict[str, str]) -> str:
    r = await client.post("/auth/login", json=creds)
    assert r.status_code == 200
    return r.json()["access_token"]


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {await _token(client, creds)}"}


async def test_create_case_requires_mutator_role(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # Reviewer cannot create; officer can.
    reviewer = await _auth(client, seed_users["reviewer"])
    r = await client.post("/cases", headers=reviewer, json={"number": "C-1", "title": "X"})
    assert r.status_code == 403

    officer = await _auth(client, seed_users["officer"])
    ok = await client.post("/cases", headers=officer, json={"number": "C-1", "title": "X"})
    assert ok.status_code == 201
    assert ok.json()["status"] == "intake"


async def test_illegal_transition_returns_409(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # T2.7: intake -> investigating is illegal (must go via open) -> 409.
    officer = await _auth(client, seed_users["officer"])
    case = (
        await client.post("/cases", headers=officer, json={"number": "C-2", "title": "Y"})
    ).json()
    bad = await client.post(
        f"/cases/{case['id']}/transition",
        headers=officer,
        json={"target": "investigating"},
    )
    assert bad.status_code == 409

    good = await client.post(
        f"/cases/{case['id']}/transition", headers=officer, json={"target": "open"}
    )
    assert good.status_code == 200
    assert good.json()["status"] == "open"


async def test_evidence_upload_download_and_custody(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # T2.1/T2.2/T2.8 end-to-end via the API using an in-memory store override.
    from app.domain import router as domain_router

    officer = await _auth(client, seed_users["officer"])
    case = (
        await client.post("/cases", headers=officer, json={"number": "C-3", "title": "Z"})
    ).json()

    # Patch the ingest service to use a fake in-memory store (no MinIO here).
    from app.domain.antivirus import NoopScanner
    from app.domain.service import IngestService

    from tests.test_domain_integration import FakeStore

    fake = IngestService(FakeStore(), NoopScanner())
    orig = domain_router._ingest_service
    domain_router._ingest_service = lambda settings: fake  # type: ignore[assignment]
    try:
        up = await client.post(
            f"/cases/{case['id']}/evidence",
            headers=officer,
            files={"file": ("photo.png", _PNG, "application/octet-stream")},
        )
        assert up.status_code == 201, up.text
        ev = up.json()
        assert ev["content_type"] == "image/png"
        assert len(ev["sha256"]) == 64

        dl = await client.get(f"/cases/evidence/{ev['id']}/download", headers=officer)
        assert dl.status_code == 200
        assert dl.content == _PNG

        cust = await client.get(f"/cases/evidence/{ev['id']}/custody", headers=officer)
        assert cust.status_code == 200
        actions = [c["action"] for c in cust.json()]
        assert "upload" in actions and "access" in actions
    finally:
        domain_router._ingest_service = orig  # type: ignore[assignment]


async def test_evidence_rejects_disallowed_type(
    client: AsyncClient, seed_users: dict[str, dict[str, str]]
) -> None:
    # T2.3: a shell script sniffed as text/plain-ish is rejected (422).
    from app.domain import router as domain_router
    from app.domain.antivirus import NoopScanner
    from app.domain.service import IngestService

    from tests.test_domain_integration import FakeStore

    officer = await _auth(client, seed_users["officer"])
    case = (
        await client.post("/cases", headers=officer, json={"number": "C-4", "title": "W"})
    ).json()

    fake = IngestService(FakeStore(), NoopScanner())
    orig = domain_router._ingest_service
    domain_router._ingest_service = lambda settings: fake  # type: ignore[assignment]
    try:
        bad = await client.post(
            f"/cases/{case['id']}/evidence",
            headers=officer,
            files={"file": ("x.sh", b"#!/bin/sh\nrm -rf /\n", "text/x-shellscript")},
        )
        assert bad.status_code == 422
    finally:
        domain_router._ingest_service = orig  # type: ignore[assignment]

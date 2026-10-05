"""Phase 0-3 hardening: cookies, CSRF reachability, audit durability, CORS,
metrics, mutation rate limiting, pagination, header injection, streaming cap.

Each test here corresponds to a gap found in review, not to a phase checklist
item, so each is written to fail if the specific defect returns.
"""

from __future__ import annotations

import base64
import json
import zipfile

import pytest
from httpx import AsyncClient

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


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


# --- P1.B2: cookie auth must actually be reachable -----------------------


async def test_login_sets_auth_cookies(client: AsyncClient, seed_users) -> None:
    """Previously login returned tokens but set no cookie, so the entire
    cookie/CSRF path was unreachable in production."""
    r = await client.post("/auth/login", json=seed_users["officer"])
    assert r.status_code == 200
    assert "access_token" in r.cookies
    assert "refresh_token" in r.cookies
    assert "csrf_token" in r.cookies


async def test_cookie_auth_works_without_bearer_header(client: AsyncClient, seed_users) -> None:
    """A browser holding only cookies must authenticate."""
    await client.post("/auth/login", json=seed_users["officer"])
    me = await client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "officer"


async def test_csrf_token_round_trip_allows_cookie_mutation(
    client: AsyncClient, seed_users
) -> None:
    """The intended double-submit flow must succeed: echo the csrf cookie in the
    header. Previously there was no way to ever get a 2xx on a cookie mutation."""
    await client.post("/auth/login", json=seed_users["officer"])
    token = client.cookies.get("csrf_token")
    assert token
    out = await client.post("/auth/logout", headers={"X-CSRF-Token": token})
    assert out.status_code == 204


async def test_csrf_missing_header_still_rejected(client: AsyncClient, seed_users) -> None:
    """The exemption for session-establishing endpoints must not have been
    implemented by weakening CSRF everywhere else."""
    await client.post("/auth/login", json=seed_users["officer"])
    assert (await client.post("/auth/logout")).status_code == 403
    assert (await client.post("/auth/logout", headers={"X-CSRF-Token": "wrong"})).status_code == 403


async def test_access_cookie_is_httponly_and_csrf_cookie_is_not(
    client: AsyncClient, seed_users
) -> None:
    r = await client.post("/auth/login", json=seed_users["officer"])
    raw = r.headers.get_list("set-cookie")
    joined = "\n".join(raw)
    access_line = next(c for c in raw if c.startswith("access_token="))
    csrf_line = next(c for c in raw if c.startswith("csrf_token="))
    assert "HttpOnly" in access_line
    assert "HttpOnly" not in csrf_line  # frontend must be able to read+echo it
    assert "samesite" in joined.lower()


async def test_logout_clears_every_cookie(client: AsyncClient, seed_users) -> None:
    await client.post("/auth/login", json=seed_users["officer"])
    token = client.cookies.get("csrf_token")
    out = await client.post("/auth/logout", headers={"X-CSRF-Token": token})
    assert out.status_code == 204
    cleared = out.headers.get_list("set-cookie")
    for name in ("access_token=", "refresh_token=", "csrf_token="):
        assert any(c.startswith(name) for c in cleared), name

    # Clearing the cookies must actually end the session, not just tidy up.
    assert (await client.get("/auth/me")).status_code == 401


# --- Session invalidation ------------------------------------------------


async def test_deactivated_user_token_stops_working(
    client: AsyncClient, seed_users, sessionmaker_fixture
) -> None:
    """A signature-valid token is not proof the account is still enabled."""
    from app.auth.repository import get_user_by_email

    headers = await _auth(client, seed_users["officer"])
    assert (await client.get("/auth/me", headers=headers)).status_code == 200

    async with sessionmaker_fixture() as session:
        user = await get_user_by_email(session, seed_users["officer"]["email"])
        user.is_active = False
        await session.commit()

    assert (await client.get("/auth/me", headers=headers)).status_code == 401


async def test_role_change_takes_effect_without_relogin(
    client: AsyncClient, seed_users, sessionmaker_fixture
) -> None:
    """A demotion must not wait for the old token to expire."""
    from app.auth.repository import get_user_by_email

    headers = await _auth(client, seed_users["officer"])
    ok = await client.post("/cases", headers=headers, json={"number": "RC-1", "title": "T"})
    assert ok.status_code == 201

    async with sessionmaker_fixture() as session:
        user = await get_user_by_email(session, seed_users["officer"]["email"])
        user.role = "reviewer"
        await session.commit()

    assert (
        await client.post("/cases", headers=headers, json={"number": "RC-2", "title": "T"})
    ).status_code == 403


# --- P2.B5/T2.5: audit durability ---------------------------------------


async def test_integrity_failure_audit_row_is_persisted(
    client: AsyncClient, seed_users, fake_store, sessionmaker_fixture
) -> None:
    """The tamper event is the single most important thing the system records.
    It was flushed and then rolled back when the route raised 409."""
    from app.domain.models import AuditLog
    from sqlalchemy import select

    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "AU-1", "title": "T"})
    ).json()["id"]
    up = await client.post(
        f"/cases/{cid}/evidence",
        headers=officer,
        files={"file": ("p.png", _PNG, "application/octet-stream")},
    )
    digest = up.json()["sha256"]

    # Corrupt the stored object behind the store's back.
    fake_store._store._objects[digest] = b"TAMPERED"  # noqa: SLF001
    resp = await client.get(f"/cases/evidence/{up.json()['id']}/download", headers=officer)
    assert resp.status_code == 409

    async with sessionmaker_fixture() as session:
        actions = set((await session.execute(select(AuditLog.action))).scalars().all())
    assert "evidence.integrity_fail" in actions


async def test_audit_chain_survives_many_appends(client: AsyncClient, seed_users) -> None:
    """Chain integrity over a realistic number of interleaved operations."""
    admin = await _auth(client, seed_users["admin"])
    for i in range(5):
        await client.post("/cases", headers=admin, json={"number": f"AV-{i}", "title": "T"})
    status = await client.get("/cases/audit/verify", headers=admin)
    assert status.status_code == 200
    assert status.json()["valid"] is True
    assert status.json()["entries"] >= 5


async def test_audit_endpoint_is_admin_only(client: AsyncClient, seed_users) -> None:
    """The audit log spans every case, so it cannot be case-scoped."""
    reviewer = await _auth(client, seed_users["reviewer"])
    officer = await _auth(client, seed_users["officer"])
    assert (await client.get("/cases/audit", headers=reviewer)).status_code == 403
    assert (await client.get("/cases/audit", headers=officer)).status_code == 403
    admin = await _auth(client, seed_users["admin"])
    assert (await client.get("/cases/audit", headers=admin)).status_code == 200


async def test_audit_entries_include_chain_fields(client: AsyncClient, seed_users) -> None:
    admin = await _auth(client, seed_users["admin"])
    await client.post("/cases", headers=admin, json={"number": "AC-F1", "title": "T"})
    body = (await client.get("/cases/audit", headers=admin)).json()
    assert body
    entry = body[0]
    for field in ("id", "action", "actor_id", "prev_hash", "entry_hash"):
        assert field in entry
    assert len(entry["entry_hash"]) == 64


# --- Notification resilience -------------------------------------------


async def test_assignment_survives_a_dead_notifier(
    client: AsyncClient, seed_users, sessionmaker_fixture
) -> None:
    """Redis down must not 500 an already-committed, already-audited action."""
    from app.auth.repository import get_user_by_email
    from app.domain import router as domain_router

    class DeadNotifier:
        async def publish(self, event, payload):
            raise ConnectionError("redis is down")

    original = domain_router._NOTIFIER
    domain_router._NOTIFIER = DeadNotifier()
    try:
        officer = await _auth(client, seed_users["officer"])
        cid = (
            await client.post("/cases", headers=officer, json={"number": "NT-1", "title": "T"})
        ).json()["id"]
        async with sessionmaker_fixture() as session:
            target = str((await get_user_by_email(session, seed_users["expert"]["email"])).id)
        resp = await client.post(
            f"/cases/{cid}/assign", headers=officer, json={"assigned_to": target}
        )
        assert resp.status_code == 200
        assert resp.json()["assigned_to"] == target
    finally:
        domain_router._NOTIFIER = original


# --- P1.B4: mutation rate limiting is actually applied ------------------


async def test_mutation_rate_limit_applies_to_case_routes(
    client: AsyncClient, seed_users, app
) -> None:
    """`rate_limit_mutation_max` was configured but read by nothing."""
    officer = await _auth(client, seed_users["officer"])
    assert (
        await client.post("/cases", headers=officer, json={"number": "RL-0", "title": "T"})
    ).status_code == 201

    # Swap the limiter in only after login, otherwise /auth/login is itself
    # limited and we never reach the case route under test.
    app.state.rate_limiter = _AlwaysLimited()
    resp = await client.post("/cases", headers=officer, json={"number": "RL-1", "title": "T"})
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After")


async def test_rate_limiter_failure_does_not_500(client: AsyncClient, seed_users, app) -> None:
    """A limiter that raises must not take the API down with it."""

    class Exploding:
        async def hit(self, key, *, max_hits, window_seconds):
            raise RuntimeError("redis gone")

    officer = await _auth(client, seed_users["officer"])
    app.state.rate_limiter = Exploding()
    resp = await client.post("/cases", headers=officer, json={"number": "RL-2", "title": "T"})
    assert resp.status_code == 201  # fails open, never 500


class _AlwaysLimited:
    async def hit(self, key, *, max_hits, window_seconds):
        return False, 42


# --- Pagination ---------------------------------------------------------


async def test_case_list_paginates(client: AsyncClient, seed_users) -> None:
    officer = await _auth(client, seed_users["officer"])
    for i in range(5):
        await client.post("/cases", headers=officer, json={"number": f"PG-{i}", "title": "T"})
    page1 = await client.get("/cases", headers=officer, params={"limit": 2})
    page2 = await client.get("/cases", headers=officer, params={"limit": 2, "offset": 2})
    assert len(page1.json()) == 2
    assert len(page2.json()) == 2
    ids1 = {c["id"] for c in page1.json()}
    ids2 = {c["id"] for c in page2.json()}
    assert not (ids1 & ids2)


async def test_pagination_bounds_are_enforced(client: AsyncClient, seed_users) -> None:
    officer = await _auth(client, seed_users["officer"])
    assert (await client.get("/cases", headers=officer, params={"limit": 0})).status_code == 422
    assert (
        await client.get("/cases", headers=officer, params={"limit": 10_000})
    ).status_code == 422
    assert (await client.get("/cases", headers=officer, params={"offset": -1})).status_code == 422


async def test_search_query_length_is_bounded(client: AsyncClient, seed_users) -> None:
    officer = await _auth(client, seed_users["officer"])
    assert (
        await client.get("/cases/search/query", headers=officer, params={"q": "a" * 10_000})
    ).status_code == 422


# --- Header injection ---------------------------------------------------


async def test_case_number_rejects_header_injection(client: AsyncClient, seed_users) -> None:
    """`case.number` was interpolated raw into Content-Disposition."""
    admin = await _auth(client, seed_users["admin"])
    hostile = 'x"; filename="pwned'
    resp = await client.post("/cases", headers=admin, json={"number": hostile, "title": "T"})
    assert resp.status_code == 422

    crlf = "a\r\nX-Injected: 1"
    assert (
        await client.post("/cases", headers=admin, json={"number": crlf, "title": "T"})
    ).status_code == 422


async def test_export_content_disposition_is_safe(client: AsyncClient, seed_users) -> None:
    admin = await _auth(client, seed_users["admin"])
    cid = (
        await client.post(
            "/cases",
            headers=admin,
            json={"number": "CASE-2026-001", "title": "T"},
        )
    ).json()["id"]
    for fmt, ext in (("json", "json"), ("csv", "csv"), ("zip", "zip")):
        r = await client.get(f"/cases/{cid}/export", headers=admin, params={"fmt": fmt})
        assert r.status_code == 200
        cd = r.headers["content-disposition"]
        assert cd == f'attachment; filename="CASE-2026-001.{ext}"'


async def test_export_rejects_unknown_format(client: AsyncClient, seed_users) -> None:
    admin = await _auth(client, seed_users["admin"])
    cid = (
        await client.post("/cases", headers=admin, json={"number": "EX-BAD", "title": "T"})
    ).json()["id"]
    resp = await client.get(f"/cases/{cid}/export", headers=admin, params={"fmt": "exe"})
    assert resp.status_code == 422


async def test_export_is_audited(client: AsyncClient, seed_users) -> None:
    admin = await _auth(client, seed_users["admin"])
    cid = (
        await client.post("/cases", headers=admin, json={"number": "EX-AU", "title": "T"})
    ).json()["id"]
    await client.get(f"/cases/{cid}/export", headers=admin, params={"fmt": "json"})
    entries = (await client.get("/cases/audit", headers=admin)).json()
    assert any(e["action"] == "case.export" for e in entries)


# --- Streaming upload cap ----------------------------------------------


async def test_oversize_upload_is_rejected_before_buffering(
    client: AsyncClient, seed_users, fake_store, monkeypatch
) -> None:
    """The 200 MiB cap was checked *after* `await file.read()`, so it capped
    nothing. Lower the cap so the test stays fast and assert the boundary."""
    import app.domain.ingest as ingest

    monkeypatch.setattr(ingest, "MAX_EVIDENCE_BYTES", len(_PNG) + 10)
    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "SZ-1", "title": "T"})
    ).json()["id"]
    resp = await client.post(
        f"/cases/{cid}/evidence",
        headers=officer,
        files={"file": ("big.png", _PNG + b"\x00" * 4096, "application/octet-stream")},
    )
    assert resp.status_code == 422
    assert "maximum size" in resp.json()["detail"]


# --- Workflow completeness ---------------------------------------------


async def test_timeline_can_be_written(client: AsyncClient, seed_users) -> None:
    """`TimelineEvent` had a table and a read route but no write path."""
    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "TL-1", "title": "T"})
    ).json()["id"]
    assert (await client.get(f"/cases/{cid}/timeline", headers=officer)).json() == []

    made = await client.post(
        f"/cases/{cid}/timeline",
        headers=officer,
        json={
            "label": "Door forced",
            "occurred_at": "2026-01-02T03:04:05Z",
            "source_ref": "evidence/abc",
            "uncertain": True,
        },
    )
    assert made.status_code == 201
    assert made.json()["uncertain"] is True

    listed = (await client.get(f"/cases/{cid}/timeline", headers=officer)).json()
    assert len(listed) == 1
    assert listed[0]["label"] == "Door forced"


async def test_finding_can_be_updated_and_deleted(client: AsyncClient, seed_users) -> None:
    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "FD-1", "title": "T"})
    ).json()["id"]
    f = (
        await client.post(
            f"/cases/{cid}/findings", headers=officer, json={"title": "draft", "body": "b"}
        )
    ).json()
    assert f["status"] == "draft"

    patched = await client.patch(
        f"/cases/{cid}/findings/{f['id']}",
        headers=officer,
        json={"status": "final", "body": "confirmed"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "final"
    assert patched.json()["body"] == "confirmed"

    assert (
        await client.delete(f"/cases/{cid}/findings/{f['id']}", headers=officer)
    ).status_code == 204
    assert (await client.get(f"/cases/{cid}/findings", headers=officer)).json() == []


async def test_entity_can_be_deleted(client: AsyncClient, seed_users) -> None:
    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "EN-1", "title": "T"})
    ).json()["id"]
    e = (
        await client.post(
            f"/cases/{cid}/entities",
            headers=officer,
            json={"kind": "ip", "value": "10.0.0.1", "attributes": {"note": "n"}},
        )
    ).json()
    assert (
        await client.delete(f"/cases/{cid}/entities/{e['id']}", headers=officer)
    ).status_code == 204


async def test_child_of_another_case_is_404(client: AsyncClient, seed_users) -> None:
    """A finding id from case A must not be patchable through case B."""
    officer = await _auth(client, seed_users["officer"])
    a = (
        await client.post("/cases", headers=officer, json={"number": "CA-1", "title": "A"})
    ).json()["id"]
    b = (
        await client.post("/cases", headers=officer, json={"number": "CA-2", "title": "B"})
    ).json()["id"]
    f = (await client.post(f"/cases/{a}/findings", headers=officer, json={"title": "f"})).json()
    assert (
        await client.patch(f"/cases/{b}/findings/{f['id']}", headers=officer, json={"body": "x"})
    ).status_code == 404
    assert (
        await client.delete(f"/cases/{b}/findings/{f['id']}", headers=officer)
    ).status_code == 404


async def test_case_metadata_is_updatable_and_visible(client: AsyncClient, seed_users) -> None:
    """`priority`/`due_at`/`assigned_to` were write-never or invisible."""
    officer = await _auth(client, seed_users["officer"])
    created = (
        await client.post("/cases", headers=officer, json={"number": "UP-1", "title": "Old"})
    ).json()
    assert created["priority"] == "normal"
    assert "assigned_to" in created and "due_at" in created

    patched = await client.patch(
        f"/cases/{created['id']}",
        headers=officer,
        json={"title": "New", "priority": "urgent", "due_at": "2026-03-01T00:00:00Z"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "New"
    assert patched.json()["priority"] == "urgent"
    assert patched.json()["due_at"] is not None


async def test_entity_and_timeline_writes_are_audited(client: AsyncClient, seed_users) -> None:
    """Entities were created without an audit row, unlike findings."""
    officer = await _auth(client, seed_users["officer"])
    admin = await _auth(client, seed_users["admin"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "AA-1", "title": "T"})
    ).json()["id"]
    await client.post(
        f"/cases/{cid}/entities", headers=officer, json={"kind": "ip", "value": "1.1.1.1"}
    )
    await client.post(f"/cases/{cid}/timeline", headers=officer, json={"label": "event"})
    actions = {e["action"] for e in (await client.get("/cases/audit", headers=admin)).json()}
    assert "entity.create" in actions
    assert "timeline.create" in actions


async def test_zip_export_still_wellformed(client: AsyncClient, seed_users, fake_store) -> None:
    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "ZP-1", "title": "T"})
    ).json()["id"]
    await client.post(
        f"/cases/{cid}/evidence",
        headers=officer,
        files={"file": ("p.png", _PNG, "application/octet-stream")},
    )
    z = await client.get(f"/cases/{cid}/export", headers=officer, params={"fmt": "zip"})
    with zipfile.ZipFile(__import__("io").BytesIO(z.content)) as zf:
        names = set(zf.namelist())
        assert {"case.json", "evidence.csv", "manifest.json"} <= names
        json.loads(zf.read("manifest.json"))


# --- P0: observability + CORS (the frontend could not call the API) -----


def test_metrics_endpoint_exists_for_prometheus() -> None:
    """infra/observability/prometheus.yml scrapes /metrics; it 404'd."""
    from app.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(Settings(secret_key="s" * 30, minio_endpoint=""))
    with TestClient(app) as c:
        r = c.get("/metrics")
    assert r.status_code == 200
    assert "forensight_http_requests_total" in r.text


def test_cors_preflight_allows_configured_origin() -> None:
    """The Next.js frontend on :3000 got 405 and no ACAO header."""
    from app.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(Settings(secret_key="s" * 30, minio_endpoint=""))
    with TestClient(app) as c:
        r = c.options(
            "/cases",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert r.headers["access-control-allow-credentials"] == "true"


def test_cors_does_not_allow_wildcard_with_credentials() -> None:
    from app.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(Settings(secret_key="s" * 30, minio_endpoint="", cors_origins=["*"]))
    with TestClient(app) as c:
        r = c.options(
            "/cases",
            headers={
                "Origin": "http://evil.test",
                "Access-Control-Request-Method": "POST",
            },
        )
    # A wildcard is invalid with credentials; the request must not be authorized.
    assert r.headers.get("access-control-allow-origin") != "*"


async def test_unknown_origin_gets_no_cors_header(client: AsyncClient) -> None:
    r = await client.get("/healthz", headers={"Origin": "http://evil.test"})
    assert r.headers.get("access-control-allow-origin") is None


async def test_security_headers_still_present_with_cors(
    client: AsyncClient,
) -> None:
    """Adding CORSMiddleware must not have displaced the header middleware."""
    r = await client.get("/healthz")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in r.headers

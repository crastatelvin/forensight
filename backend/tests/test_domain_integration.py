"""Phase 2 integration tests: ingest dedupe, custody, audit chain (T2.1/T2.5/T2.8).

Uses a fake in-memory object store so no live MinIO is required for these; the
real WORM behavior (T2.6) is exercised separately against MinIO.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from app.domain.antivirus import NoopScanner
from app.domain.repository import (
    append_audit,
    custody_for_evidence,
    verify_audit_chain,
)
from app.domain.service import IngestService

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class FakeStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def ensure_bucket(self) -> None:
        return None

    def object_exists(self, key: str) -> bool:
        return key in self._objects

    def put_evidence(self, *, key: str, data: bytes, content_type: str) -> str:
        # WORM semantics: never overwrite an existing key.
        if key not in self._objects:
            self._objects[key] = data
        return key

    def get_evidence(self, key: str) -> bytes:
        return self._objects[key]


@pytest.fixture
async def domain_session(sessionmaker_fixture):
    async with sessionmaker_fixture() as session:
        yield session


async def _make_case(session) -> uuid.UUID:
    from app.domain.models import Case

    case = Case(
        number=f"C-{uuid.uuid4().hex[:6]}",
        title="Test case",
        owner_id=uuid.uuid4(),
    )
    session.add(case)
    await session.flush()
    return case.id


async def test_ingest_dedupes_by_content_hash(domain_session) -> None:
    # T2.1: identical bytes dedupe to the same evidence/object.
    svc = IngestService(FakeStore(), NoopScanner())
    case_id = await _make_case(domain_session)
    actor = uuid.uuid4()

    e1 = await svc.ingest(
        domain_session, case_id=case_id, actor_id=actor, filename="a.png", data=_PNG
    )
    e2 = await svc.ingest(
        domain_session, case_id=case_id, actor_id=actor, filename="copy.png", data=_PNG
    )
    assert e1.id == e2.id
    assert e1.sha256 == e2.sha256


async def test_download_reverifies_hash_and_records_custody(domain_session) -> None:
    # T2.1 + T2.8: download re-verifies hash and logs a custody access entry.
    svc = IngestService(FakeStore(), NoopScanner())
    case_id = await _make_case(domain_session)
    actor = uuid.uuid4()

    ev = await svc.ingest(
        domain_session, case_id=case_id, actor_id=actor, filename="a.png", data=_PNG
    )
    fetched, data = await svc.download(domain_session, evidence_id=ev.id, actor_id=actor)
    assert data == _PNG
    custody = await custody_for_evidence(domain_session, ev.id)
    actions = [c.action for c in custody]
    assert "upload" in actions
    assert "access" in actions  # T2.8: every access recorded with actor+action


async def test_audit_chain_verifies_and_detects_tamper(domain_session) -> None:
    # T2.5: chain verifies; tampering a row breaks verification.
    actor = uuid.uuid4()
    await append_audit(domain_session, actor_id=actor, action="a", target="t1", payload={"n": 1})
    e2 = await append_audit(
        domain_session, actor_id=actor, action="b", target="t2", payload={"n": 2}
    )
    await append_audit(domain_session, actor_id=actor, action="c", target="t3", payload={"n": 3})
    assert await verify_audit_chain(domain_session) is True

    # Tamper with a committed row's payload.
    e2.payload = {"n": 999}
    await domain_session.flush()
    assert await verify_audit_chain(domain_session) is False

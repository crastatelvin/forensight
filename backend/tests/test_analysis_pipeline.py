"""Phase 4 tests: idempotency, job lifecycle, checksums, capability gating.

Each test maps to a checklist item and is written so it fails if that specific
defect returns. The reproducibility property (T4.1/T4.6) is what makes results
admissible, so it gets the most attention.
"""

from __future__ import annotations

import ast
import base64
import pathlib
import uuid

import pytest
from app.analysis import registry
from app.analysis.executor import execute_job
from app.analysis.models import AnalysisJob
from app.analysis.repository import submit_job
from app.analysis.types import (
    ModelChecksumError,
    ModuleSpec,
    Tier,
    UnsupportedCapability,
    build_result,
    idempotency_key,
)
from httpx import AsyncClient
from sqlalchemy import func, select

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure the built-in module is registered before each test.

    `create_app` calls `register_all()`, and tests that swap in a fake module
    call `registry.reset()` first — so without this, ordering would decide
    whether `byte-entropy` exists.
    """
    if not registry.is_registered("byte-entropy"):
        registry.reset()
        registry.register_all()


@pytest.fixture
def fake_store():
    """Patch both the ingest service *and* the executor's evidence reader.

    The executor builds its own ObjectStore, so patching only the ingest service
    (as the Phase 2/3 tests do) is not enough — the job would still try to reach
    MinIO. This patches the executor too, keyed off the same in-memory store.
    """
    from app.analysis import executor as executor_mod
    from app.domain import router as domain_router
    from app.domain.antivirus import NoopScanner
    from app.domain.service import IngestService

    from tests.test_domain_integration import FakeStore

    store = FakeStore()
    service = IngestService(store, NoopScanner())
    original_service = domain_router._ingest_service
    original_fetch = executor_mod._read_from_object_store
    domain_router._ingest_service = lambda settings: service
    executor_mod._read_from_object_store = lambda evidence, settings: (
        store.get_evidence(evidence.storage_key)
    )
    try:
        yield service
    finally:
        domain_router._ingest_service = original_service
        executor_mod._read_from_object_store = original_fetch


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    r = await client.post("/auth/login", json=creds)
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _case_with_evidence(
    client: AsyncClient, headers: dict[str, str], fake_store, number: str
) -> tuple[str, str]:
    cid = (
        await client.post("/cases", headers=headers, json={"number": number, "title": "T"})
    ).json()["id"]
    up = await client.post(
        f"/cases/{cid}/evidence",
        headers=headers,
        files={"file": ("p.png", _PNG, "application/octet-stream")},
    )
    assert up.status_code == 201, up.text
    return cid, up.json()["id"]


# --- P4.B3: the idempotency key ------------------------------------------


def test_key_is_stable_for_identical_inputs() -> None:
    a = idempotency_key(
        evidence_sha256="abc",
        evidence_id="ev1",
        module_name="m",
        params={"x": 1, "y": 2},
        model_version="1.0.0",
    )
    b = idempotency_key(
        evidence_sha256="abc",
        evidence_id="ev1",
        module_name="m",
        params={"y": 2, "x": 1},
        model_version="1.0.0",
    )
    assert a == b, "param order must not change the key"


@pytest.mark.parametrize(
    "change",
    [
        {"evidence_sha256": "def"},
        {"evidence_id": "ev2"},
        {"module_name": "n"},
        {"params": {"x": 2, "y": 2}},
        {"params": {}},
        {"model_version": "2.0.0"},
    ],
)
def test_key_changes_when_any_input_changes(change: dict) -> None:
    """Every input that can alter the output must be in the key, or caching
    silently returns a stale score for different work."""
    base = {
        "evidence_sha256": "abc",
        "evidence_id": "ev1",
        "module_name": "m",
        "params": {"x": 1, "y": 2},
        "model_version": "1.0.0",
    }
    assert idempotency_key(**base) != idempotency_key(**{**base, **change})


# --- T4.1: same input cached, changed version recomputes -----------------


async def test_same_request_reuses_the_same_job(
    client: AsyncClient, seed_users, fake_store
) -> None:
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "ID-1")

    first = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid, "params": {"a": 1}},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid, "params": {"a": 1}},
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"], "identical work must reuse"

    jobs = (await client.get(f"/cases/{cid}/analyses", headers=officer)).json()
    assert len(jobs) == 1, f"expected one job, got {len(jobs)}"


async def test_changed_model_version_creates_a_new_job(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """Bumping model_version is how cached results get invalidated."""
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "ID-2")

    first = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid},
    )
    assert first.status_code == 201

    original = registry.get("byte-entropy")
    bumped = _versioned_module("byte-entropy", "2.0.0")
    registry.reset()
    registry.register(bumped)

    second = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid},
    )
    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["model_version"] == "2.0.0"
    del original


async def test_different_params_create_different_jobs(
    client: AsyncClient, seed_users, fake_store
) -> None:
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "ID-3")

    a = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid, "params": {"m": 1}},
    )
    b = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid, "params": {"m": 2}},
    )
    assert a.json()["id"] != b.json()["id"]


def _versioned_module(name: str, version: str):
    class _M:
        spec = ModuleSpec(
            name=name, version=version, tier=Tier.B, threshold=7.5, requires_gpu=False
        )

        def __call__(self, data: bytes, params: dict) -> dict:
            return build_result(self.spec, score=0.0, detail={})

    return _M()


# --- T4.6: N identical jobs collapse under a race ------------------------


async def test_concurrent_identical_submissions_collapse_to_one(
    client: AsyncClient, seed_users, fake_store, sessionmaker_fixture
) -> None:
    """The UNIQUE constraint is what makes this hold; a check-then-insert would
    let every racer see "no job yet" and insert its own."""
    officer = await _auth(client, seed_users["officer"])
    actor_id = (await client.get("/auth/me", headers=officer)).json()["id"]
    cid, eid = await _case_with_evidence(client, officer, fake_store, "RACE-1")

    async with sessionmaker_fixture() as session:
        from app.domain.models import Evidence

        evidence = await session.get(Evidence, uuid.UUID(eid))
        assert evidence is not None

        async def _submit():
            return await submit_job(
                session,
                case_id=uuid.UUID(cid),
                evidence_id=evidence.id,
                evidence_sha256=evidence.sha256,
                module="byte-entropy",
                module_version="1.0.0",
                params={"p": 1},
                requested_by=uuid.UUID(actor_id),
                gpu_available=False,
            )

        # Same session sequentially: the second must reuse, never duplicate.
        first, created_first = await _submit()
        second, created_second = await _submit()
        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        await session.commit()

    async with sessionmaker_fixture() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(AnalysisJob)
                .where(AnalysisJob.case_id == uuid.UUID(cid))
            )
        ).scalar_one()
    assert count == 1, f"identical work must collapse to one job, got {count}"


async def test_unique_constraint_rejects_duplicate_key(
    sessionmaker_fixture,
) -> None:
    """Belt-and-braces: prove the database, not just the code, prevents dupes."""
    key = idempotency_key(
        evidence_sha256="a" * 64,
        evidence_id=str(uuid.uuid4()),
        module_name="m",
        params={},
        model_version="1",
    )
    async with sessionmaker_fixture() as session:
        from app.domain.models import Case

        case = Case(number=f"U-{uuid.uuid4().hex[:6]}", title="t", owner_id=uuid.uuid4())
        session.add(case)
        await session.flush()

        from app.analysis.models import AnalysisJob as Job

        session.add(
            Job(
                case_id=case.id,
                evidence_id=case.id,
                module="m",
                model_version="1",
                idempotency_key=key,
                requested_by=case.owner_id,
            )
        )
        await session.commit()

        session.add(
            Job(
                case_id=case.id,
                evidence_id=case.id,
                module="m",
                model_version="1",
                idempotency_key=key,
                requested_by=case.owner_id,
            )
        )
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await session.commit()


# --- T4.2: job lifecycle -------------------------------------------------


async def test_job_lifecycle_queued_running_done(
    client: AsyncClient, seed_users, fake_store
) -> None:
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "LC-1")

    submitted = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid},
    )
    job_id = submitted.json()["id"]
    assert submitted.json()["status"] == "queued"

    ran = await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)
    assert ran.status_code == 200
    body = ran.json()
    assert body["status"] == "done"
    assert body["started_at"] is not None
    assert body["finished_at"] is not None
    assert body["attempts"] == 1
    assert body["error"] == ""


async def test_failed_module_records_error_and_leaves_job_failed(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """A bad module must be recorded, not raised: the worker has to survive."""

    class _Exploding:
        spec = ModuleSpec(name="boom", version="1.0.0", tier=Tier.A, threshold=0.5)

        def __call__(self, data: bytes, params: dict) -> dict:
            raise RuntimeError("kaboom")

    registry.reset()
    registry.register(_Exploding())

    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "LC-2")
    submitted = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "boom", "evidence_id": eid},
    )
    job_id = submitted.json()["id"]

    ran = await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)
    assert ran.status_code == 200, "a failing module must not 500 the runner"
    assert ran.json()["status"] == "failed"
    assert "kaboom" in ran.json()["error"]


async def test_module_returning_no_score_is_a_failure(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """A result with no score cannot be reported as a score."""

    class _NoScore:
        spec = ModuleSpec(name="silent", version="1.0.0", tier=Tier.A, threshold=0.5)

        def __call__(self, data: bytes, params: dict) -> dict:
            return {"nothing": "useful"}

    registry.reset()
    registry.register(_NoScore())
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "LC-3")
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "silent", "evidence_id": eid},
        )
    ).json()["id"]
    ran = await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)
    assert ran.json()["status"] == "failed"
    assert "score" in ran.json()["error"]


async def test_rerunning_a_terminal_job_is_a_no_op(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """Queue redelivery must not re-run completed work."""
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "LC-4")
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "byte-entropy", "evidence_id": eid},
        )
    ).json()["id"]
    first = await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)
    second = await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)
    assert first.json()["status"] == second.json()["status"] == "done"
    assert second.json()["attempts"] == first.json()["attempts"] == 1


# --- T4.4: capability gating --------------------------------------------


def test_gpu_module_refused_on_cpu_host() -> None:
    module = _gpu_module()
    registry.check_capability(module, gpu_available=True)  # fine on GPU
    with pytest.raises(UnsupportedCapability):
        registry.check_capability(module, gpu_available=False)


async def test_gpu_job_marked_unsupported_not_failed(
    client: AsyncClient, seed_users, fake_store, app
) -> None:
    """Recorded as `unsupported` so nothing suggests a bug, and never as a
    faked CPU approximation."""
    registry.reset()
    registry.register(_gpu_module())

    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "CAP-1")
    submitted = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "needs-gpu", "evidence_id": eid},
    )
    assert submitted.json()["status"] == "unsupported"
    assert "GPU" in submitted.json()["unsupported_reason"]

    results = (await client.get(f"/cases/{cid}/results", headers=officer)).json()
    assert results == [], "an unsupported module must not produce a score"


def _gpu_module():
    class _GPU:
        spec = ModuleSpec(
            name="needs-gpu",
            version="1.0.0",
            tier=Tier.A,
            threshold=0.5,
            requires_gpu=True,
        )

        def __call__(self, data: bytes, params: dict) -> dict:
            return build_result(self.spec, score=0.99, detail={"gpu": True})

    return _GPU()


async def test_modules_endpoint_reports_unavailable_with_reason(
    client: AsyncClient, seed_users
) -> None:
    """The UI must be able to explain why a module cannot run."""
    registry.reset()
    registry.register(_gpu_module())
    admin = await _auth(client, seed_users["admin"])
    mods = (await client.get("/modules", headers=admin)).json()
    gpu = next(m for m in mods if m["name"] == "needs-gpu")
    assert gpu["available"] is False
    assert "GPU" in gpu["unavailable_reason"]
    assert gpu["requires_gpu"] is True


# --- T4.5: result schema ------------------------------------------------


async def test_result_always_carries_threshold_version_and_tier(
    client: AsyncClient, seed_users, fake_store
) -> None:
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "RS-1")
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "byte-entropy", "evidence_id": eid},
        )
    ).json()["id"]
    await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)

    results = (await client.get(f"/cases/{cid}/results", headers=officer)).json()
    assert len(results) == 1
    r = results[0]
    for field in ("score", "threshold", "model_version", "tier", "disclaimer"):
        assert r[field] is not None and r[field] != "", f"missing {field}"
    assert r["tier"] in ("A", "B")
    assert "NOT an identity match" in r["disclaimer"] or "Not courtroom" in r["disclaimer"]


async def test_module_cannot_omit_mandatory_fields(
    client: AsyncClient, seed_users, fake_store, sessionmaker_fixture
) -> None:
    """A module returning a partial dict still gets a complete stored result:
    build_result injects the mandatory fields server-side."""

    class _Partial:
        spec = ModuleSpec(name="partial", version="3.1.4", tier=Tier.A, threshold=0.75)

        def __call__(self, data: bytes, params: dict) -> dict:
            return {"score": 0.9}  # no threshold, no version, no tier

    registry.reset()
    registry.register(_Partial())

    from app.analysis.repository import get_job, list_results_for_case
    from app.config import Settings

    settings = Settings(secret_key="s" * 30, minio_endpoint="")
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "RS-2")
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "partial", "evidence_id": eid},
        )
    ).json()["id"]

    # execute_job sanitizes the module's payload, so drive it directly.
    async with sessionmaker_fixture() as session:
        job = await get_job(session, uuid.UUID(job_id))
        assert job is not None
        await execute_job(
            session,
            job,
            settings=settings,
            gpu_available=False,
            evidence_reader=lambda evidence: _PNG,
        )
        assert job.status == "done"
        results = await list_results_for_case(session, uuid.UUID(cid))
        assert len(results) == 1
        r = results[0]
        assert r.threshold == 0.75
        assert r.model_version == "3.1.4"
        assert r.tier == "A"
        assert r.disclaimer


def test_tier_b_disclaimer_says_not_identity() -> None:
    """The honesty clause has to be in the payload, not only in docs."""
    spec = ModuleSpec(name="m", version="1", tier=Tier.B, threshold=0.5)
    payload = build_result(spec, 0.1, {})
    assert "NOT an identity match" in payload["disclaimer"]


# --- P4.B5 / T4.3: model checksum verification ---------------------------


def test_checksum_mismatch_refuses_load() -> None:
    from app.analysis.models_registry import ModelStore

    class _FakeClient:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def get_object(self, bucket: str, key: str):  # noqa: ANN201
            payload = self._data

            class _Resp:
                def read(self) -> bytes:
                    return payload

                def close(self) -> None:
                    pass

                def release_conn(self) -> None:
                    pass

            return _Resp()

    import hashlib

    from app.analysis.types import ModelChecksumError as MCE

    good = b"model-weights-v1"
    store = ModelStore.__new__(ModelStore)
    store._settings = None
    store._client = _FakeClient(good)
    correct = hashlib.sha256(good).hexdigest()
    assert store.load_verified(key="m/1/w", expected_sha256=correct) == good

    store._client = _FakeClient(b"TAMPERED")
    with pytest.raises(MCE, match="checksum mismatch"):
        store.load_verified(key="m/1/w", expected_sha256=correct)


async def test_job_fails_when_model_checksum_fails(
    client: AsyncClient, seed_users, fake_store, sessionmaker_fixture
) -> None:
    """A wrong model must fail the job, never fall back to a different one."""

    class _NeedsModel:
        spec = ModuleSpec(
            name="model-user",
            version="1.0.0",
            tier=Tier.A,
            threshold=0.5,
            model_key="m/1.0.0/weights.bin",
            model_sha256="0" * 64,
        )

        def __call__(self, data: bytes, params: dict) -> dict:
            return build_result(self.spec, 0.5, {})

    registry.reset()
    registry.register(_NeedsModel())

    class _BadStore:
        def load_verified(self, *, key: str, expected_sha256: str) -> bytes:
            raise ModelChecksumError(f"model {key!r} checksum mismatch")

    from app.analysis.repository import get_job
    from app.config import Settings

    settings = Settings(secret_key="s" * 30, minio_endpoint="")
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "MC-1")
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "model-user", "evidence_id": eid},
        )
    ).json()["id"]

    # Drive the executor directly so the failing store is injectable.
    async with sessionmaker_fixture() as session:
        job = await get_job(session, uuid.UUID(job_id))
        assert job is not None
        await execute_job(
            session,
            job,
            settings=settings,
            gpu_available=False,
            model_store=_BadStore(),
        )
        assert job.status == "failed"
        assert "checksum mismatch" in job.error


# --- determinism (supports T4.1) ----------------------------------------


def test_entropy_module_is_deterministic() -> None:
    module = registry.get("byte-entropy")
    a = module(_PNG, {})
    b = module(_PNG, {})
    assert a["score"] == b["score"]
    assert a == b


def test_entropy_of_known_input() -> None:
    """Golden value: uniform bytes are maximum entropy, all-same bytes are zero."""
    import math

    module = registry.get("byte-entropy")
    assert module(b"\x00" * 100, {})["score"] == pytest.approx(0.0)
    uniform = module(bytes(range(256)) * 4, {})["score"]
    assert uniform == pytest.approx(8.0, abs=1e-9)
    assert math.isclose(uniform, 8.0, abs_tol=1e-9)


#: Modules whose *own* behaviour is nondeterministic. `numpy` is deliberately
#: absent: numpy arithmetic is fully deterministic and is required for real image
#: work. What matters is that none of its random entry points are used.
NONDETERMINISTIC_MODULES = {"random", "time", "uuid", "secrets", "os", "socket"}

#: Fully-qualified paths whose result varies between runs on identical input.
NONDETERMINISTIC_CALLS = {
    "random.uniform",
    "random.randint",
    "random.choice",
    "random.sample",
    "random.shuffle",
    "random.random",
    "np.random.random",
    "np.random.uniform",
    "np.random.randint",
    "np.random.choice",
    "np.random.shuffle",
    "np.random.normal",
    "numpy.random.random",
    "numpy.random.default_rng",
    "numpy.random.seed",
    "time.time",
    "time.monotonic",
    "datetime.now",
    "datetime.today",
    "datetime.utcnow",
    "uuid.uuid4",
    "uuid.uuid1",
    "uuid.uuid3",
    "os.urandom",
    "secrets.token_hex",
    "secrets.token_urlsafe",
}


def _dotted(node: ast.AST) -> str:
    """Render an attribute/call chain as a dotted path, or '' if unresolvable."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _scan_module(path, tree: ast.AST, failures: list[str]) -> None:
    """Record every nondeterminism source found in one module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in NONDETERMINISTIC_MODULES:
                    failures.append(f"{path}:{node.lineno} imports {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if root in NONDETERMINISTIC_MODULES:
                failures.append(f"{path}:{node.lineno} imports from {module!r}")
            if root == "numpy" and "random" in module:
                failures.append(f"{path}:{node.lineno} imports numpy.random")
        elif isinstance(node, ast.Call):
            dotted = _dotted(node.func)
            if dotted in NONDETERMINISTIC_CALLS:
                failures.append(f"{path}:{node.lineno} calls {dotted}()")
            if dotted.endswith(".default_rng") or dotted.endswith(".RandomState"):
                failures.append(f"{path}:{node.lineno} constructs {dotted}()")


def test_no_module_has_a_nondeterminism_source() -> None:
    """T4.1/T6.6 guard: an analyzer whose output varies between runs on identical
    input cannot be admissible, whatever it computes.

    Parsed rather than grepped, so a docstring mentioning "random" is fine and a
    buried `np.random.normal` is not.
    """
    failures: list[str] = []
    for path in sorted(pathlib.Path("app/analysis/modules").rglob("*.py")):
        if path.name == "__init__.py":
            continue
        _scan_module(path, ast.parse(path.read_text()), failures)
    assert not failures, "nondeterminism in analyzer modules:\n  " + "\n  ".join(failures)


def test_nondeterminism_scanner_actually_catches_things() -> None:
    """A guard that cannot fail is not a guard. Feeds it known-bad source."""
    bad = [
        "import random\nrandom.uniform(0, 1)\n",
        "import numpy as np\nx = np.random.normal(0, 1)\n",
        "from numpy.random import default_rng\n",
        "import time\nt = time.time()\n",
        "import uuid\nu = uuid.uuid4()\n",
        "from datetime import datetime\nd = datetime.now()\n",
        "import os\nk = os.urandom(4)\n",
        "import secrets\nt = secrets.token_hex(8)\n",
    ]
    for source in bad:
        failures: list[str] = []
        _scan_module("<test>", ast.parse(source), failures)
        assert failures, f"scanner missed: {source.strip()!r}"


def test_nondeterminism_scanner_allows_deterministic_science() -> None:
    """The guard must not block legitimate numeric work."""
    good = [
        "import numpy as np\nx = np.mean(arr)\n",
        "from PIL import Image\nimport numpy as np\n",
        "import hashlib\nh = hashlib.sha256(data).hexdigest()\n",
        "import math\nm = math.log2(2)\n",
        "# we do not use random numbers here\nimport numpy\n",
    ]
    for source in good:
        failures: list[str] = []
        _scan_module("<test>", ast.parse(source), failures)
        assert not failures, f"scanner false-positived on: {source.strip()!r} -> {failures}"


# --- authorization on analysis routes ------------------------------------


async def test_analysis_routes_are_case_scoped(client: AsyncClient, seed_users, fake_store) -> None:
    """Another officer's evidence cannot be analysed."""
    officer = await _auth(client, seed_users["officer"])
    expert = await _auth(client, seed_users["expert"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "AU-1")

    denied = await client.post(
        f"/cases/{cid}/analyses",
        headers=expert,
        json={"module": "byte-entropy", "evidence_id": eid},
    )
    assert denied.status_code == 404
    assert (await client.get(f"/cases/{cid}/analyses", headers=expert)).status_code == 404
    assert (await client.get(f"/cases/{cid}/results", headers=expert)).status_code == 404
    assert (await client.get(f"/evidence/{eid}/results", headers=expert)).status_code == 404


async def test_reviewer_can_read_results_but_not_submit(
    client: AsyncClient, seed_users, fake_store
) -> None:
    officer = await _auth(client, seed_users["officer"])
    reviewer = await _auth(client, seed_users["reviewer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "AU-2")
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "byte-entropy", "evidence_id": eid},
        )
    ).json()["id"]
    await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)

    assert (await client.get(f"/cases/{cid}/results", headers=reviewer)).status_code == 200
    assert (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=reviewer,
            json={"module": "byte-entropy", "evidence_id": eid},
        )
    ).status_code == 403


async def test_evidence_from_another_case_is_rejected(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """The evidence must belong to the case named in the URL."""
    officer = await _auth(client, seed_users["officer"])
    cid_a, eid_a = await _case_with_evidence(client, officer, fake_store, "XC-1")
    cid_b = (
        await client.post("/cases", headers=officer, json={"number": "XC-2", "title": "B"})
    ).json()["id"]
    resp = await client.post(
        f"/cases/{cid_b}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid_a},
    )
    assert resp.status_code == 404
    assert cid_a != cid_b


async def test_unknown_module_is_rejected(client: AsyncClient, seed_users, fake_store) -> None:
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "UM-1")
    resp = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "does-not-exist", "evidence_id": eid},
    )
    assert resp.status_code == 422
    assert "byte-entropy" in resp.json()["detail"], "should list valid modules"


# --- audit ---------------------------------------------------------------


async def test_submission_and_run_are_audited(client: AsyncClient, seed_users, fake_store) -> None:
    officer = await _auth(client, seed_users["officer"])
    admin = await _auth(client, seed_users["admin"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "AA-1")
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "byte-entropy", "evidence_id": eid},
        )
    ).json()["id"]
    await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)

    actions = {e["action"] for e in (await client.get("/cases/audit", headers=admin)).json()}
    assert "analysis.submit" in actions
    assert "analysis.run" in actions


async def test_reuse_is_audited_distinctly(client: AsyncClient, seed_users, fake_store) -> None:
    """A cache hit is a different event from a new submission."""
    officer = await _auth(client, seed_users["officer"])
    admin = await _auth(client, seed_users["admin"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "AA-2")
    body = {"module": "byte-entropy", "evidence_id": eid}
    await client.post(f"/cases/{cid}/analyses", headers=officer, json=body)
    await client.post(f"/cases/{cid}/analyses", headers=officer, json=body)

    actions = [e["action"] for e in (await client.get("/cases/audit", headers=admin)).json()]
    assert "analysis.submit" in actions
    assert "analysis.reuse" in actions


# --- queue behaviour -----------------------------------------------------


async def test_no_broker_configured_leaves_job_queued(
    client: AsyncClient, seed_users, fake_store, app
) -> None:
    """Submission must succeed without a broker; the job stays runnable."""
    app.state.settings.queue_url = ""
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "Q-1")
    resp = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "queued"


async def test_broker_outage_does_not_fail_submission(
    client: AsyncClient, seed_users, fake_store, app, monkeypatch
) -> None:
    """A dead broker must not 500 a request that already committed the job."""
    from app.analysis import queue as queue_mod

    def _boom(job_id, *, settings):  # noqa: ANN001, ANN202
        raise ConnectionError("redis down")

    monkeypatch.setattr(queue_mod, "enqueue_analysis_job", _boom)
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "Q-2")
    resp = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "byte-entropy", "evidence_id": eid},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "queued"


def test_enqueue_returns_false_without_broker() -> None:
    from app.analysis.queue import enqueue_analysis_job, is_configured
    from app.config import Settings

    settings = Settings(secret_key="s" * 30, minio_endpoint="", queue_url="")
    assert is_configured(settings) is False
    assert enqueue_analysis_job(uuid.uuid4(), settings=settings) is False


async def test_identical_bytes_in_different_cases_do_not_share_a_job(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """Two cases holding byte-identical evidence get their own jobs.

    The content hash alone would be enough to reproduce a score, but a job is
    also a record inside a case: sharing one row across cases would put case A's
    work in case B's listing and audit log.
    """
    officer = await _auth(client, seed_users["officer"])
    cid_a, eid_a = await _case_with_evidence(client, officer, fake_store, "SAME-A")
    cid_b, eid_b = await _case_with_evidence(client, officer, fake_store, "SAME-B")
    assert eid_a != eid_b

    ja = (
        await client.post(
            f"/cases/{cid_a}/analyses",
            headers=officer,
            json={"module": "byte-entropy", "evidence_id": eid_a},
        )
    ).json()["id"]
    jb = (
        await client.post(
            f"/cases/{cid_b}/analyses",
            headers=officer,
            json={"module": "byte-entropy", "evidence_id": eid_b},
        )
    ).json()["id"]
    assert ja != jb, "a job must not be shared across cases"

    jobs_a = (await client.get(f"/cases/{cid_a}/analyses", headers=officer)).json()
    assert [j["id"] for j in jobs_a] == [ja]


# --- P6: real image analyzers through the full pipeline ---------------------


async def test_image_ela_runs_end_to_end_and_is_flagged(
    client: AsyncClient, seed_users, fake_store, app
) -> None:
    """The whole path: upload -> job -> worker executor -> persisted result.

    Unit tests call `image_ela.run` directly. This proves the module is
    registered in the app, is reachable by name over HTTP, produces a score, and
    that the score survives the round trip through the database.
    """
    from tests.fixtures_images import make_tampered_jpeg

    registry.reset()
    registry.register_all()

    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "ELA-1", "title": "T"})
    ).json()["id"]
    up = await client.post(
        f"/cases/{cid}/evidence",
        headers=officer,
        files={"file": ("tampered.jpg", make_tampered_jpeg(), "image/jpeg")},
    )
    assert up.status_code == 201, up.text
    eid = up.json()["id"]

    submitted = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "image-ela", "evidence_id": eid},
    )
    assert submitted.json()["status"] == "queued"
    job_id = submitted.json()["id"]

    ran = await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)
    assert ran.json()["status"] == "done", ran.text

    results = (await client.get(f"/cases/{cid}/results", headers=officer)).json()
    ela = [r for r in results if r["module"] == "image-ela"]
    assert len(ela) == 1, results
    assert ela[0]["score"] > ela[0]["threshold"], "the tampered fixture must be flagged"
    assert ela[0]["model_version"]
    assert ela[0]["tier"] == "A"


async def test_not_applicable_job_records_no_result(
    client: AsyncClient, seed_users, fake_store, app
) -> None:
    """T6.2 at the pipeline level: ELA on a PNG must not become a 0.0 score.

    The distinction that matters downstream is "cannot measure" versus "measured
    zero". A report that rendered this as 0.0 would be telling an examiner the
    image showed no sign of manipulation, when in fact ELA never ran.
    """
    registry.reset()
    registry.register_all()

    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "ELA-2")

    submitted = await client.post(
        f"/cases/{cid}/analyses",
        headers=officer,
        json={"module": "image-ela", "evidence_id": eid},
    )
    job_id = submitted.json()["id"]
    ran = await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)

    body = ran.json()
    assert body["status"] == "not_applicable", body
    assert "PNG" in body["unsupported_reason"]
    assert body["attempts"] == 1, "not_applicable is a real run, not a failure"

    results = (await client.get(f"/cases/{cid}/results", headers=officer)).json()
    assert results == [], "cannot-measure must not be recorded as a measurement"


async def test_image_exif_runs_end_to_end_on_clean_evidence(
    client: AsyncClient, seed_users, fake_store, app
) -> None:
    """A clean camera photo scores zero through the full pipeline."""
    from tests.fixtures_images import CAMERA_EXIF, exif_bytes

    registry.reset()
    registry.register_all()

    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "EXIF-1", "title": "T"})
    ).json()["id"]
    up = await client.post(
        f"/cases/{cid}/evidence",
        headers=officer,
        files={"file": ("clean.jpg", exif_bytes(CAMERA_EXIF), "image/jpeg")},
    )
    eid = up.json()["id"]
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "image-exif", "evidence_id": eid},
        )
    ).json()["id"]

    ran = await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)
    assert ran.json()["status"] == "done", ran.text

    results = (await client.get(f"/cases/{cid}/results", headers=officer)).json()
    exif = [r for r in results if r["module"] == "image-exif"]
    assert len(exif) == 1
    assert exif[0]["score"] == 0.0


async def test_same_image_twice_hits_the_idempotency_key(
    client: AsyncClient, seed_users, fake_store, app
) -> None:
    """T6.5: the second submission must not create a second job."""
    from tests.fixtures_images import make_tampered_jpeg

    registry.reset()
    registry.register_all()

    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "ELA-3", "title": "T"})
    ).json()["id"]
    up = await client.post(
        f"/cases/{cid}/evidence",
        headers=officer,
        files={"file": ("t.jpg", make_tampered_jpeg(), "image/jpeg")},
    )
    eid = up.json()["id"]
    payload = {"module": "image-ela", "evidence_id": eid}

    first = (await client.post(f"/cases/{cid}/analyses", headers=officer, json=payload)).json()
    second = (await client.post(f"/cases/{cid}/analyses", headers=officer, json=payload)).json()
    assert first["id"] == second["id"]
    assert second["attempts"] == 0

    await client.post(f"/analyses/jobs/{first['id']}/run", headers=officer)
    results = (await client.get(f"/cases/{cid}/results", headers=officer)).json()
    assert len(results) == 1


async def test_verified_model_bytes_reach_the_module(
    client: AsyncClient, seed_users, fake_store, app, sessionmaker_fixture
) -> None:
    """P6: the checksum-verified artifact must be handed to the module.

    Verifying a digest and then discarding the bytes would leave the guarantee
    hollow: the module would have to fetch its own weights by some other route,
    and the check would attest to an artifact nothing used. This asserts the
    module receives exactly the bytes whose digest was pinned.
    """
    registry.reset()

    received: dict[str, object] = {}

    class _WeightsModule:
        spec = ModuleSpec(
            name="weights-user",
            version="1.0.0",
            tier=Tier.A,
            threshold=0.5,
            model_key="m/1/w",
            model_sha256="deadbeef",
        )

        def __call__(self, data: bytes, params: dict, *, model_bytes: bytes | None = None) -> dict:
            received["model_bytes"] = model_bytes
            received["data"] = data
            return build_result(self.spec, score=0.5, detail={})

    registry.register(_WeightsModule())

    from app.analysis.repository import get_job
    from app.config import Settings

    class _GoodStore:
        def load_verified(self, *, key: str, expected_sha256: str) -> bytes:
            received["key"] = key
            received["expected_sha256"] = expected_sha256
            return b"THE-VERIFIED-WEIGHTS"

    settings = Settings(secret_key="s" * 30, minio_endpoint="")
    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "MW-1")
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "weights-user", "evidence_id": eid},
        )
    ).json()["id"]

    async with sessionmaker_fixture() as session:
        job = await get_job(session, uuid.UUID(job_id))
        assert job is not None
        await execute_job(
            session,
            job,
            settings=settings,
            gpu_available=False,
            model_store=_GoodStore(),
        )
        assert job.status == "done", job.error

    assert received["model_bytes"] == b"THE-VERIFIED-WEIGHTS"
    assert received["key"] == "m/1/w"
    assert received["expected_sha256"] == "deadbeef"
    assert received["data"] is not None


async def test_module_without_weights_still_runs(
    client: AsyncClient, seed_users, fake_store, app
) -> None:
    """A pure-algorithm module gets `model_bytes=None` and is unaffected.

    Adding the keyword to the call site must not break the modules that have no
    weights, or every existing analyzer would need editing for no reason.
    """
    registry.reset()
    registry.register_all()

    officer = await _auth(client, seed_users["officer"])
    cid, eid = await _case_with_evidence(client, officer, fake_store, "MW-2")
    job_id = (
        await client.post(
            f"/cases/{cid}/analyses",
            headers=officer,
            json={"module": "byte-entropy", "evidence_id": eid},
        )
    ).json()["id"]

    ran = await client.post(f"/analyses/jobs/{job_id}/run", headers=officer)
    assert ran.json()["status"] == "done", ran.text
    results = (await client.get(f"/cases/{cid}/results", headers=officer)).json()
    assert [r["module"] for r in results] == ["byte-entropy"]

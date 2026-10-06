"""Phase 0 tests for health/readiness endpoints and the SECRET_KEY guard.

Covers:
- T0.1 (readiness reflects dependency reachability; here deps are stubbed)
- T0.4 (app refuses to boot if SECRET_KEY unset)
- liveness always ok
"""

from __future__ import annotations

import pytest
from app.config import Settings
from app.health import DependencyStatus
from app.main import MissingSecretKeyError, create_app
from fastapi.testclient import TestClient


def _settings(**overrides: object) -> Settings:
    base = {
        "secret_key": "test-secret-key-not-real",
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
        "redis_url": "redis://localhost:6379/0",
        "minio_endpoint": "localhost:9000",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_healthz_liveness_ok() -> None:
    app = create_app(_settings())
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_missing_secret_key_refuses_boot() -> None:
    # T0.4: no SECRET_KEY -> app must not be constructed.
    with pytest.raises(MissingSecretKeyError):
        create_app(_settings(secret_key=""))


def test_readyz_returns_503_when_dependency_down(monkeypatch: pytest.MonkeyPatch) -> None:
    # T0.1: readiness is 503 when any dependency is unreachable.
    async def fake_gather(_settings: Settings) -> list[DependencyStatus]:
        return [
            DependencyStatus("postgres", True),
            DependencyStatus("redis", False, "connection refused"),
            DependencyStatus("minio", True),
        ]

    monkeypatch.setattr("app.main.gather_readiness", fake_gather)
    app = create_app(_settings())
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert body["dependencies"]["redis"]["ok"] is False


def test_readyz_returns_200_when_all_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # T0.1: readiness is 200 only when all dependencies reachable.
    async def fake_gather(_settings: Settings) -> list[DependencyStatus]:
        return [
            DependencyStatus("postgres", True),
            DependencyStatus("redis", True),
            DependencyStatus("minio", True),
        ]

    monkeypatch.setattr("app.main.gather_readiness", fake_gather)
    app = create_app(_settings())
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


def test_capabilities_reflect_the_analyzers_that_ship() -> None:
    """The list is the registry, not a hand-maintained copy.

    These two tests previously asserted on a static list that had drifted: they
    passed while `/capabilities` advertised three GPU analyzers that do not exist
    and omitted the five that do. The contract they actually wanted -- "the list
    reflects what can run here" -- is now stated directly, and the per-capability
    detail lives in tests/test_analysis_pipeline.py.
    """
    app = create_app(_settings(gpu_available=False))
    with TestClient(app) as client:
        resp = client.get("/capabilities")
    assert resp.status_code == 200
    body = resp.json()

    from app.analysis import registry

    registry.register_all()
    shipped = {s.name for s in registry.all_specs()}
    reported = {c["name"] for c in body["capabilities"]}
    assert reported == shipped, f"{sorted(reported)} != {sorted(shipped)}"


def test_every_unavailable_capability_states_a_reason() -> None:
    """An unavailable capability that does not say why is just a lie.

    This is the T0.5 degradation contract, now stated against real analyzers
    rather than placeholder names. Every shipped analyzer is CPU-only today, so
    nothing should be gated -- and that is worth asserting, because it is what
    caught the stale list: it claimed three GPU analyzers that do not exist.
    """
    app = create_app(_settings(gpu_available=False))
    with TestClient(app) as client:
        body = client.get("/capabilities").json()

    for cap in body["capabilities"]:
        if cap["available"]:
            continue
        assert cap.get("reason"), f"unavailable without a reason: {cap}"
    assert all(c["available"] for c in body["capabilities"]), body["capabilities"]

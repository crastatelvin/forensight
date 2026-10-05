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


def test_capabilities_gpu_unavailable_reports_degraded() -> None:
    # T0.5: with GPU absent, GPU modules are reported unavailable (not faked).
    app = create_app(_settings(gpu_available=False))
    with TestClient(app) as client:
        resp = client.get("/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gpu_available"] is False
    gpu_caps = [c for c in body["capabilities"] if c["requires_gpu"]]
    cpu_caps = [c for c in body["capabilities"] if not c["requires_gpu"]]
    assert gpu_caps, "expected at least one GPU-requiring capability"
    assert all(c["available"] is False for c in gpu_caps)
    assert all("reason" in c for c in gpu_caps)
    # CPU analyzers remain available on a CPU-only host.
    assert cpu_caps and all(c["available"] is True for c in cpu_caps)


def test_capabilities_gpu_available_enables_modules() -> None:
    app = create_app(_settings(gpu_available=True))
    with TestClient(app) as client:
        resp = client.get("/capabilities")
    body = resp.json()
    assert body["gpu_available"] is True
    gpu_caps = [c for c in body["capabilities"] if c["requires_gpu"]]
    assert gpu_caps and all(c["available"] is True for c in gpu_caps)

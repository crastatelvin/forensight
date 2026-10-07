"""T10.6 — the constrained-host profile: CPU analyzers run, GPU ones say so.

An `android-dev` profile models a laptop or a phone-sized host. The claim it makes is
not "it works, just slower" — it is that **CPU-only is a supported deployment**, which
needs two properties that are easy to confuse:

1. **The CPU analyzers are not approximations.** They are the same pinned weights run
   by onnxruntime rather than by a CUDA provider. A profile that substituted a
   smaller or different model for CPU would be a simulation, which is the one thing
   this project refuses anywhere else.
2. **The GPU-only analyzers report themselves unavailable *before* a job is
   submitted.** Not a 500 at execution time, and not a plausible-looking score.

The second is the one that matters operationally. A capability list that advertises a
GPU module on a CPU host is a promise the deployment cannot keep, and it is the exact
bug this project already found and fixed once in the hand-written capability list.

The compose wiring is asserted too — a profile that starts no worker can pass every
capability test and still never analyse anything.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml
from app.capabilities import get_capabilities
from app.config import Settings
from httpx import AsyncClient

COMPOSE = pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _settings(**overrides: object) -> Settings:
    return Settings(
        **{  # type: ignore[arg-type]
            "secret_key": "x" * 40,
            "minio_endpoint": "",
            "gpu_available": False,
            **overrides,
        }
    )


# --- what a CPU-only host can and cannot do -------------------------------


def test_cpu_hosts_report_every_gpu_module_as_unavailable() -> None:
    """A GPU module on a CPU host is unavailable, and says why.

    The reason is what separates "this host cannot run this" from "you have not
    finished installing it" — two problems with two different fixes.
    """
    capabilities = {c.name: c for c in get_capabilities(_settings())}

    for capability in capabilities.values():
        if capability.requires_gpu:
            assert (
                not capability.available
            ), f"{capability.name} requires a GPU but reports available on a CPU host"
            assert "GPU" in capability.reason or "gpu" in capability.reason.lower()


def test_cpu_hosts_still_report_the_onnx_analyzers_as_available() -> None:
    """CPU-only is a *supported* deployment, so this must not be empty.

    An earlier version of this project's capability list advertised three GPU
    capabilities no shipped analyzer implemented, while omitting the five analyzers
    that do — a list that had drifted from the code. Asserting the CPU set is
    non-empty means a profile that silently stopped working is a test failure rather
    than a deployment nobody notices.
    """
    available = {c.name for c in get_capabilities(_settings()) if c.available}
    assert available, "no analyzer is available on a CPU host"
    # The three ONNX-backed ones this profile is built around.
    for expected in ("byte-entropy", "image-exif", "object-detect"):
        assert expected in available, f"{expected} should run on CPU; available: {available}"


def test_the_same_module_reports_differently_on_a_gpu_host() -> None:
    """The capability list is not a constant.

    Without this, `available` could be hardcoded and the CPU test above would pass
    while the GPU path reported nonsense.
    """
    cpu = {c.name: c.available for c in get_capabilities(_settings())}
    gpu = {c.name: c.available for c in get_capabilities(_settings(gpu_available=True))}

    assert set(cpu) == set(gpu), "the same modules are listed on both hosts"
    assert all(gpu[name] for name in gpu), (
        "a GPU host reports a module unavailable: " f"{[n for n, v in gpu.items() if not v]}"
    )


def test_capabilities_carry_a_version_and_a_tier() -> None:
    """A capability list without versions cannot answer "are these comparable?".

    And without a tier it cannot answer the question this whole project exists
    about, which is how far the number goes.
    """
    for capability in get_capabilities(_settings()):
        assert capability.version, f"{capability.name} reports no version"
        assert capability.tier in {"A", "B"}, (
            f"{capability.name} reports tier {capability.tier!r}, which no result can "
            f"carry; a capability list advertising a tier nothing produces is the "
            f"same class of drift as the hand-written list it replaced"
        )


# --- the profile itself ---------------------------------------------------


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


@pytest.mark.parametrize("service", ["api", "worker", "ollama", "postgres", "redis"])
def test_the_android_profile_can_actually_run_the_platform(service: str) -> None:
    """The profile must contain a worker.

    This is not a formality. `worker` was in `core` only, so `android-dev` started a
    database and an LLM with nothing to run an analysis: `/capabilities` would report
    the analyzers available, jobs would queue and never be claimed, and the first
    symptom would be a case that quietly never finished. A dev profile that cannot run
    the product is worse than no dev profile.
    """
    profiles = _compose()["services"][service].get("profiles", [])
    assert "android-dev" in profiles, f"{service} is not in the android-dev profile"


def test_the_android_profile_shares_no_gpu() -> None:
    """No GPU is passed to any container in the profile.

    `GPU_AVAILABLE=false` alone is a *claim*; the absence of a GPU device mapping is
    the fact. A profile that sets the flag while still passing `/dev/dri` would let a
    GPU module start and then fail at execution — the failure mode the capability
    list is supposed to prevent.
    """
    services = _compose()["services"]
    for name, service in services.items():
        if "android-dev" not in service.get("profiles", []):
            continue
        devices = service.get("devices") or []
        assert not devices, f"{name} exposes {devices} in the android-dev profile"
        assert "/dev/dri" not in str(
            service.get("volumes", [])
        ), f"{name} mounts a GPU device in the android-dev profile"


def test_no_gpu_worker_is_started_by_the_android_profile() -> None:
    """The GPU worker is deliberately not in it.

    Including it would produce jobs the profile has no way to run, which is the
    failure this profile exists to demonstrate does not happen.
    """
    assert "android-dev" not in _compose()["services"]["worker-gpu"].get("profiles", [])


def test_ollama_is_capped_for_a_constrained_host() -> None:
    """Parallelism and resident models are capped.

    A phone-sized host that lets Ollama keep two models resident swaps or OOMs, and
    the symptom is the agent failing intermittently rather than a clear refusal.
    """
    ollama = _compose()["services"]["ollama"]["environment"]
    assert ollama.get("OLLAMA_MAX_LOADED_MODELS") == "1"
    assert ollama.get("OLLAMA_NUM_PARALLEL") == "1"


def test_the_worker_uses_the_same_image_as_the_api() -> None:
    """They cannot drift on the domain layer they share.

    A separate worker image is how the worker ends up serialising a result payload
    the API cannot read — and the failure appears only on jobs that cross the
    boundary, which is all of them, only sometimes.
    """
    services = _compose()["services"]
    assert services["worker"]["image"] == services["api"]["image"]
    assert services["worker-gpu"]["image"] == services["api"]["image"]


# --- what the running profile reports -------------------------------------


async def test_capabilities_endpoint_agrees_with_the_computed_list(
    client: AsyncClient, seed_users: dict
) -> None:
    """The endpoint and the function must not disagree.

    The endpoint once re-serialised each field by hand, and a field added to
    `Capability` silently did not appear in the response — which is how `tier` went
    missing once already. Asserting the endpoint against the function is what catches
    the next one.
    """
    login = await client.post("/auth/login", json=seed_users["officer"])
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.get("/capabilities", headers=headers)
    assert response.status_code == 200, response.text
    served = {c["name"]: c for c in response.json()["capabilities"]}

    computed = {c.name: c for c in get_capabilities(_settings())}
    assert set(served) == set(computed), (
        f"endpoint lists {sorted(set(served) - set(computed))} but the function "
        f"computes {sorted(set(computed) - set(served))}"
    )
    for name, capability in served.items():
        assert capability["available"] == computed[name].available, name
        assert capability["tier"] == computed[name].tier, f"{name} lost its tier"
        assert capability["version"] == computed[name].version, f"{name} lost its version"

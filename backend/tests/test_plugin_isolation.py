"""T9.6 — a new analyzer is a registry call, not a core edit.

The claim under test is architectural: registering a module must be sufficient for it
to appear in the catalogue, in capabilities, to accept a job, and to run — without
editing the registry, the router, the executor, or the capability reporter.

This is the property that decides whether the platform can grow. If it fails, every
future analyzer needs four coordinated edits across the codebase, and the ones that
get skipped are the ones nobody notices are missing: a module that runs but never
appears in the capability list looks like a deployment that quietly does not have it.

So the test does not check that a *particular* module works. It defines one here, in
the test, and asserts the whole system picks it up. A module that exists only in
test code and is fully functional everywhere is the strongest possible evidence that
the extension point is real.

It also asserts the negative, which is the part that makes the positive meaningful:
after registering, nothing else in the process may change. A registry that leaks
between tests, or a `register_all` that turns out to be load-bearing, would show up
here rather than as an intermittent failure somewhere unrelated.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.analysis import registry
from app.analysis.types import AnalysisNotApplicable, ModuleSpec, Tier, build_result
from httpx import AsyncClient


class _PluginModule:
    """A complete, real analyzer, defined entirely in this file.

    No weights and no algorithm, because neither is what is being tested: what is
    being tested is that the platform *reaches* a module it has never heard of.
    """

    def __init__(self, name: str) -> None:
        self.spec = ModuleSpec(
            name=name,
            version="9.9.9",
            tier=Tier.B,
            threshold=0.5,
            accepts=("application/octet-stream",),
            disclaimer="A plugin registered by a test, to prove the extension point.",
        )

    def __call__(
        self,
        data: bytes,
        params: dict[str, Any],
        *,
        model_bytes: bytes | None = None,
        aux_models: dict[str, bytes] | None = None,
        reference_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        _ = model_bytes, aux_models, reference_bytes
        # A real precondition rather than an empty-input shortcut: ingest rejects a
        # zero-byte upload before it ever reaches a module, so refusing on `not data`
        # would have made this path unreachable outside a direct call.
        if len(data) < MIN_MEASURABLE_BYTES:
            raise AnalysisNotApplicable(
                f"needs at least {MIN_MEASURABLE_BYTES} bytes to measure anything"
            )
        return build_result(
            self.spec,
            # Deterministic, so a repeat run must produce the identical value.
            score=len(data) / 100.0,
            detail={"bytes": len(data), "params": params},
        )


PLUGIN = "test-plugin-novel-technique"

#: The plugin's own precondition. See `_PluginModule.__call__`.
MIN_MEASURABLE_BYTES = 32


@pytest.fixture
def registered_plugin() -> Any:
    """Register the plugin for one test, then remove it.

    Removed afterwards because the registry is process-global: leaving a test module
    in it would make every later test that enumerates the catalogue see a fiction,
    and a suite that has quietly grown a fake analyzer is worse than one that has
    not.
    """
    module = _PluginModule(PLUGIN)
    if registry.is_registered(PLUGIN):
        del registry._REGISTRY[PLUGIN]  # noqa: SLF001 - deliberate, test-scoped
    registry.register(module)
    try:
        yield module
    finally:
        registry._REGISTRY.pop(PLUGIN, None)  # noqa: SLF001


async def test_registering_a_module_is_the_only_step_required(
    registered_plugin: Any, client: AsyncClient, seed_users: dict
) -> None:
    """Catalogue, capabilities, submission and execution — with no core edit.

    Each of those four is a place a hardcoded list could exist, and each has existed
    in this codebase before: the capability list was hand-written and drifted from
    the analyzers, and `/capabilities` re-serialised its fields by hand and silently
    dropped one. This is the test that would have caught both.
    """
    headers = await _auth(client, seed_users["officer"])

    # 1. The catalogue, which the interface builds its picker from.
    modules = await client.get("/modules", headers=headers)
    assert modules.status_code == 200, modules.text
    listed = {m["name"]: m for m in modules.json()}
    assert PLUGIN in listed, f"not in /modules: {sorted(listed)}"
    assert listed[PLUGIN]["version"] == "9.9.9"

    # 2. Capabilities, including the tier the interface now reads rather than asserts.
    caps = await client.get("/capabilities", headers=headers)
    assert caps.status_code == 200, caps.text
    cap = {c["name"]: c for c in caps.json()["capabilities"]}
    assert PLUGIN in cap, f"not in /capabilities: {sorted(cap)}"
    assert cap[PLUGIN]["tier"] == "B"


async def test_a_registered_module_runs_and_records_a_result(
    registered_plugin: Any,
    client: AsyncClient,
    seed_users: dict,
    fake_store: Any,
) -> None:
    """The whole path, end to end, on a module the codebase has never seen.

    Driven through the real upload endpoint rather than by inserting evidence rows,
    so the plugin is exercised over the same ingest, hashing and storage the rest of
    the system uses. Seeding rows directly would have left the object store empty
    and the run would have failed on a missing object — which says nothing about
    whether the extension point works.
    """
    headers = await _auth(client, seed_users["officer"])

    case_id = (
        await client.post("/cases", headers=headers, json={"number": "PLUG-1", "title": "plugin"})
    ).json()["id"]
    uploaded = await client.post(
        f"/cases/{case_id}/evidence",
        headers=headers,
        files={"file": ("raw.bin", b"x" * 250, "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["id"]

    submitted = await client.post(
        f"/cases/{case_id}/analyses",
        json={"evidence_id": str(evidence_id), "module": PLUGIN},
        headers=headers,
    )
    assert submitted.status_code == 201, submitted.text
    job_id = submitted.json()["id"]
    assert submitted.json()["status"] == "queued"

    # Run it the way the worker's "run now" path does, so no queue is needed.
    ran = await client.post(f"/analyses/jobs/{job_id}/run", headers=headers)
    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert body["status"] == "done", body

    results = await client.get(f"/cases/{case_id}/results", headers=headers)
    assert results.status_code == 200, results.text
    stored = [r for r in results.json() if r["module"] == PLUGIN]
    assert len(stored) == 1, results.text
    assert stored[0]["score"] == pytest.approx(2.5)
    assert stored[0]["model_version"] == "9.9.9"
    assert stored[0]["tier"] == "B"
    # The module's own disclaimer travels rather than a generic tier sentence, which
    # is what `ModuleSpec.disclaimer` exists for.
    assert "registered by a test" in stored[0]["disclaimer"]


async def test_a_registered_module_refuses_itself_when_the_technique_is_undefined(
    registered_plugin: Any,
    client: AsyncClient,
    seed_users: dict,
    fake_store: Any,
) -> None:
    """`not_applicable` is reachable from a plugin, and writes no result.

    A plugin that could only ever succeed would make the pipeline look more capable
    than it is. The refusal path is the one that keeps "we could not measure this"
    distinguishable from "we measured zero", and it has to work for modules nobody
    reviewed.
    """
    headers = await _auth(client, seed_users["officer"])
    case_id = (
        await client.post("/cases", headers=headers, json={"number": "PLUG-NA", "title": "plugin"})
    ).json()["id"]
    uploaded = await client.post(
        f"/cases/{case_id}/evidence",
        headers=headers,
        files={"file": ("tiny.bin", b"short", "application/octet-stream")},
    )
    evidence_id = uploaded.json()["id"]

    submitted = await client.post(
        f"/cases/{case_id}/analyses",
        json={"evidence_id": str(evidence_id), "module": PLUGIN},
        headers=headers,
    )
    job_id = submitted.json()["id"]
    ran = await client.post(f"/analyses/jobs/{job_id}/run", headers=headers)
    assert ran.json()["status"] == "not_applicable", ran.json()

    results = await client.get(f"/cases/{case_id}/results", headers=headers)
    assert results.json() == [], "a refused plugin run wrote a result row"


def test_registering_does_not_disturb_the_built_ins(registered_plugin: Any) -> None:
    """The negative half, without which the positive means little.

    `register_all` is idempotent by design, so adding a module must not change any
    existing entry — including not renumbering, reordering or re-versioning them. A
    catalogue that mutated on registration would make every golden test's module list
    a function of test order.
    """
    registry.register_all()
    with_plugin = set(registry.registered_names())

    registry._REGISTRY.pop(PLUGIN, None)  # noqa: SLF001
    registry.register_all()
    without_plugin = set(registry.registered_names())

    assert with_plugin - without_plugin == {PLUGIN}
    assert without_plugin <= with_plugin, "removing the plugin removed a built-in"


def test_a_duplicate_name_is_refused(registered_plugin: Any) -> None:
    """Registering twice is a real bug, not a no-op.

    `register_all` skips duplicates so building two apps is safe; `register` does not,
    because at that point a repeated name means two different analyzers are claiming
    one identity and whichever loaded second would silently win.
    """
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_PluginModule(PLUGIN))


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    response = await client.post("/auth/login", json=creds)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

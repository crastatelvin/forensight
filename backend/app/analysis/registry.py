"""Module registry (P4.B4) and capability gating (T4.4).

A plain dict of name -> module. Not a plugin loader: discovery by scanning the
filesystem would mean import-time side effects and an unclear failure mode, and
three analyzers do not justify it. Adding a module is one line in `register_all`.
"""

from __future__ import annotations

from app.analysis.types import AnalysisModule, ModuleSpec, UnsupportedCapability

_REGISTRY: dict[str, AnalysisModule] = {}


def register(module: AnalysisModule) -> None:
    name = module.spec.name
    if name in _REGISTRY:
        raise ValueError(f"analysis module {name!r} is already registered")
    _REGISTRY[name] = module


def get(name: str) -> AnalysisModule:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"unknown analysis module {name!r}; registered: {known}") from None


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def registered_names() -> list[str]:
    return sorted(_REGISTRY)


def all_specs() -> list[ModuleSpec]:
    return [_REGISTRY[n].spec for n in sorted(_REGISTRY)]


def check_capability(module: AnalysisModule, *, gpu_available: bool) -> None:
    """Refuse to run a GPU-only module on a CPU host (T4.4).

    Raises rather than degrading: returning a CPU approximation would produce a
    score that looks like the real module's but is not, which is exactly the
    simulation this project exists to avoid. The job is recorded as
    `unsupported` instead.
    """
    if module.spec.requires_gpu and not gpu_available:
        raise UnsupportedCapability(
            f"module {module.spec.name!r} requires a GPU; this host has none"
        )


def register_all() -> None:
    """Make the built-in modules available. Explicit, not discovered.

    Idempotent on purpose: `create_app` calls this, and building two apps (as
    tests do) must not explode on a duplicate name. `register()` stays strict
    for Phase 6 plugins, where a duplicate name is a real bug worth surfacing.
    """
    from app.analysis.modules import entropy, image_ela, image_exif

    for module in (entropy.module, image_ela.module, image_exif.module):
        if not is_registered(module.spec.name):
            register(module)


def reset() -> None:
    """Clear the registry. Tests only."""
    _REGISTRY.clear()

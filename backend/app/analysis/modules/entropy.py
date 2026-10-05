"""Deterministic reference analyzer (Phase 4 proof module).

Not one of the Phase 6 Tier-A ML analyzers — this exists to make the pipeline
provable end to end with something that is genuinely real and deterministic:
byte-level Shannon entropy over an image's pixel data.

Why this module: P4.E requires the pipeline to be proven with golden inputs
before ML arrives. A random number would prove nothing about reproducibility;
entropy is a real, well-defined function of the bytes, so the same evidence
must always yield the same score, and a changed version must change it.

It is genuinely useful rather than filler: high entropy is a signal of
encrypted or compressed content, so the score is informational triage, and it is
labelled Tier B accordingly — it supports a lead, it does not identify anything.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from app.analysis.types import ModuleSpec, Tier, build_result

# Pinned by hand so the golden test has a stable expectation. Bumping this is
# what forces recomputation, since model_version is in the idempotency key.
VERSION = "1.0.0"

#: 8.0 bits/byte is the maximum for byte data. Above this, essentially nothing
#: is compressible left; below it, the file has structure. Chosen as the
#: decision bar for "this looks like encrypted or already-compressed content".
THRESHOLD = 7.5


def shannon_entropy(data: bytes) -> float:
    """Entropy in bits per byte. Pure function of `data`; no randomness."""
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def run(data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    """Score = entropy of the whole payload, 0..8."""
    entropy = shannon_entropy(data)
    spec = ModuleSpec(
        name="byte-entropy",
        version=VERSION,
        tier=Tier.B,
        threshold=THRESHOLD,
        accepts=(),
    )
    return build_result(
        spec,
        score=entropy,
        detail={
            "entropy_bits_per_byte": round(entropy, 6),
            "bytes_analyzed": len(data),
            "interpretation": (
                "high entropy: content appears encrypted or compressed"
                if entropy >= spec.threshold
                else "low entropy: content has compressible structure"
            ),
            "params": params,
        },
    )


spec = ModuleSpec(
    name="byte-entropy",
    version=VERSION,
    tier=Tier.B,
    threshold=THRESHOLD,
    accepts=(),
    params={},
    requires_gpu=False,
)


class _EntropyModule:
    """Adapter giving the module the `spec` attribute the registry expects."""

    def __init__(self) -> None:
        self.spec = spec

    def __call__(self, data: bytes, params: dict[str, Any]) -> dict[str, Any]:
        return run(data, params)


module = _EntropyModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

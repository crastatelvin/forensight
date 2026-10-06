"""Runtime capability reporting.

Surfaces which analyzers are available on this host, so a CPU-only deployment —
the `android-dev` profile, or any laptop — degrades cleanly and *reports* what is
unavailable instead of faking it (T0.5, and the plan's honesty clause).

Built from the analysis module registry rather than a hand-written list. The
static list it replaces advertised three GPU capabilities — ArcFace embeddings,
deepfake detection, speaker embeddings — that no shipped analyzer implements,
while omitting the five analyzers that do. A capability list that has drifted
from the code is worse than none: it is a promise the system does not keep.

An analyzer is unavailable when it needs a GPU the host lacks, *or* when its
pinned weights have not been provisioned into the model store. The second case is
the useful one — it is the difference between "this host cannot run this" and
"you have not finished installing it", and the two need different fixes.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings

#: Why a module is unavailable, when it is. Empty means available.
_UNAVAILABLE_MISSING_WEIGHTS = "pinned weights are not provisioned in the model store"


@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    requires_gpu: bool
    reason: str = ""
    #: The module version, so a caller can tell two builds of the same module
    #: apart. A capability list without versions cannot answer "are these results
    #: comparable?".
    version: str = ""


def get_capabilities(settings: Settings, *, weights_available: bool = True) -> list[Capability]:
    """Capabilities of the analyzers this build actually ships.

    `weights_available` is a flag rather than a live store probe on purpose: this
    is called by `/capabilities` and by the readiness path, both of which should
    not pay a 257 MB S3 round trip to render a list. A deployment that wants the
    stricter check calls `weights_available=False` from a provisioning script.
    """
    from app.analysis import registry

    registry.register_all()
    caps: list[Capability] = []
    for spec in registry.all_specs():
        if spec.requires_gpu and not settings.gpu_available:
            caps.append(
                Capability(
                    name=spec.name,
                    available=False,
                    requires_gpu=True,
                    reason="no GPU detected on this host",
                    version=spec.version,
                )
            )
        elif spec.model_key and not weights_available:
            caps.append(
                Capability(
                    name=spec.name,
                    available=False,
                    requires_gpu=False,
                    reason=_UNAVAILABLE_MISSING_WEIGHTS,
                    version=spec.version,
                )
            )
        else:
            caps.append(
                Capability(
                    name=spec.name,
                    available=True,
                    requires_gpu=spec.requires_gpu,
                    version=spec.version,
                )
            )
    return caps

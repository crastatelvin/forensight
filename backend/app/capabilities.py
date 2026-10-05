"""Runtime capability reporting.

Surfaces which analyzer classes are available given the host's capabilities
(e.g. GPU presence). This lets missing-GPU environments — such as the
`android-dev` CPU-only profile — degrade cleanly and *report* unavailable
modules instead of faking them (test T0.5, and the plan's honesty clause).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    requires_gpu: bool
    reason: str = ""


# Static capability catalog for Phase 0. Analyzer plugins register real entries
# from Phase 6 onward; for now this proves the CPU/GPU degradation contract.
_CPU_CAPABILITIES: tuple[str, ...] = (
    "image-forensics-ela",  # ELA/EXIF — pure CPU signal forensics
    "object-detection-cpu",  # YOLO CPU inference (slow but functional)
)
_GPU_CAPABILITIES: tuple[str, ...] = (
    "face-recognition-gpu",  # InsightFace/ArcFace embeddings
    "deepfake-detection-gpu",  # Xception/EfficientNet ONNX
    "speaker-embedding-gpu",  # SpeechBrain ECAPA-TDNN
)


def get_capabilities(settings: Settings) -> list[Capability]:
    caps: list[Capability] = [
        Capability(name=name, available=True, requires_gpu=False) for name in _CPU_CAPABILITIES
    ]
    for name in _GPU_CAPABILITIES:
        caps.append(
            Capability(
                name=name,
                available=settings.gpu_available,
                requires_gpu=True,
                reason="" if settings.gpu_available else "no GPU detected on this host",
            )
        )
    return caps

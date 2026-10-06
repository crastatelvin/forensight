"""Image forensics: Error Level Analysis (P6.B3).

ELA is a real, published screening technique. Re-encode a JPEG at a fixed
quality; because JPEG is lossy and parts of the image that were re-encoded
later retain *double* compression history, manipulated regions show a different
error level from untouched ones. High local error, relative to the image's own
baseline, marks regions for review.

What ELA is **not**: proof of manipulation. It has well-documented false
positives — saturated highlights, heavy grain, sharp synthetic edges, and
already-multiply-compressed files all raise the error level without anyone
editing anything. So the output is Tier A "regions to examine", never
"this image was tampered with", and the disclaimer says so in the payload.

Two deliberate choices:

- **Anomaly detection uses median + MAD, not mean + std.** The regions ELA is
  looking for are precisely the high-error ones, so they inflate the very mean and
  standard deviation a z-score would normalise against. Median absolute
  deviation is unmoved by that contamination.
- **`model_version` carries the library versions.** The score depends on the JPEG
  encoder's output, so a libjpeg-turbo upgrade would silently change every score.
  Putting Pillow and libjpeg-turbo in the version string puts them in the
  idempotency key, so such an upgrade forces recomputation instead of serving
  stale results under an unchanged label.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
from PIL import Image, features
from PIL import __version__ as PILLOW_VERSION

from app.analysis.types import (
    AnalysisNotApplicable,
    ModuleSpec,
    Tier,
    build_result,
)

ALGORITHM_VERSION = "1.0.0"

#: Re-compression quality. The ELA literature standardises on 75; changing it
#: changes every score, so it is a pinned constant, not a tunable.
ELA_QUALITY = 75

#: Flag a pixel when its error exceeds median + this multiple of the MAD. 6.0 is
#: deliberately high: ELA is a screening tool, and a low threshold buries the
#: examiner in false positives.
ELA_SIGMAS = 6.0

#: Fraction of anomalously-compressed pixels at which the image is worth
#: examining. Measured against the fixture set; see tests/test_image_forensics.py.
THRESHOLD = 0.01

#: Below this, ELA cannot say anything useful and the score would be noise.
MIN_PIXELS = 4096

#: Published limitations of ELA, surfaced in every result payload so a reader of
#: the JSON — not just a reader of this file — learns what the score cannot rule
#: out. An examiner acting on a low score must know about these.
KNOWN_LIMITATIONS: tuple[str, ...] = (
    "edge sensitivity: compositing any region leaves a discontinuity that raises "
    "the local error level, so a low score does not rule out an edit made along "
    "matching edges",
    "prior compression: an image re-saved as a whole shows elevated error "
    "levels with no edit having been made",
    "high-detail and saturated regions produce false positives regardless of " "any manipulation",
    "a low score is absence of signal, not evidence of authenticity",
)


def _versions() -> str:
    jpeg = features.version("jpg") or "unknown"
    return f"pil{PILLOW_VERSION}-libjpeg{jpeg}"


#: Version string includes the codec, so an encoder change invalidates caches.
VERSION = f"{ALGORITHM_VERSION}+ela-q{ELA_QUALITY}+{_versions()}"


def _decode(data: bytes) -> Image.Image:
    """Decode bytes, or refuse if ELA cannot apply.

    ELA is defined on JPEG re-compression. For any other format there is no
    honest score to produce, so the job is recorded as not-applicable rather than
    scored as zero.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 - undecodable input is a refusal
        raise AnalysisNotApplicable(
            f"ELA cannot be applied: the evidence did not decode as an image ({exc})"
        ) from exc

    fmt = (image.format or "").upper()
    if fmt != "JPEG":
        raise AnalysisNotApplicable(
            f"ELA is defined on JPEG re-compression; this evidence is {fmt or 'unrecognised'}"
        )
    return image


def error_levels(image: Image.Image) -> np.ndarray:
    """Per-pixel absolute error between the original and a re-encode.

    Returns a float32 array of per-pixel error levels.
    """
    rgb = image.convert("RGB")
    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=ELA_QUALITY)
    buffer.seek(0)
    recompressed = np.asarray(Image.open(buffer).convert("RGB"), dtype=np.int16)
    original = np.asarray(rgb, dtype=np.int16)
    # Mean across colour channels: a manipulation usually shifts luminance too.
    return np.abs(original - recompressed).mean(axis=2).astype(np.float32)


def analyse_levels(levels: np.ndarray) -> dict[str, Any]:
    """Score = fraction of pixels whose error is anomalous for *this* image."""
    flat = levels.reshape(-1)
    median = float(np.median(flat))
    mad = float(np.median(np.abs(flat - median)))
    # MAD collapses to 0 for a perfectly flat error field (e.g. a synthetic
    # image with no detail). Fall back to the mean absolute deviation so the
    # threshold still means something instead of dividing by zero.
    if mad == 0.0:
        mad = float(np.mean(np.abs(flat - median))) or 1.0
    cutoff = median + ELA_SIGMAS * mad
    flagged = int(np.count_nonzero(flat > cutoff))
    total = int(flat.size)
    return {
        "anomalous_pixel_fraction": flagged / total,
        "anomalous_pixels": flagged,
        "total_pixels": total,
        "median_error_level": round(median, 6),
        "mad_error_level": round(mad, 6),
        "error_cutoff": round(cutoff, 6),
        "mean_error_level": round(float(flat.mean()), 6),
        "max_error_level": round(float(flat.max()), 6),
    }


def run(data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    image = _decode(data)
    width, height = image.size
    if width * height < MIN_PIXELS:
        raise AnalysisNotApplicable(
            f"ELA needs at least {MIN_PIXELS} pixels to be meaningful; this image "
            f"is {width}x{height}"
        )

    stats = analyse_levels(error_levels(image))
    score = float(stats["anomalous_pixel_fraction"])
    spec = ModuleSpec(
        name="image-ela",
        version=VERSION,
        tier=Tier.A,
        threshold=THRESHOLD,
        accepts=("image/jpeg",),
    )
    detail: dict[str, Any] = {
        **stats,
        "width": width,
        "height": height,
        "ela_quality": ELA_QUALITY,
        "sigmas": ELA_SIGMAS,
        "pillow_version": PILLOW_VERSION,
        "libjpeg_turbo": features.version("jpg"),
        "params": params,
        "known_limitations": list(KNOWN_LIMITATIONS),
        "interpretation": (
            "elevated local error levels: regions worth examining for "
            "manipulation. Not proof of tampering — highlights, grain, sharp "
            "edges and prior compression all raise error level."
            if score >= THRESHOLD
            else "error levels consistent with uniform compression history"
        ),
    }
    return build_result(spec, score=score, detail=detail)


spec = ModuleSpec(
    name="image-ela",
    version=VERSION,
    tier=Tier.A,
    threshold=THRESHOLD,
    accepts=("image/jpeg",),
    params={},
    requires_gpu=False,
)


class _ElaModule:
    def __init__(self) -> None:
        self.spec = spec

    def __call__(
        self,
        data: bytes,
        params: dict[str, Any],
        *,
        model_bytes: bytes | None = None,
        aux_models: dict[str, bytes] | None = None,
        reference_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        # This module is a pure algorithm: no weights, so model_bytes is unused.
        return run(data, params)


module = _ElaModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

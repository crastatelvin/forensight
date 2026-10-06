"""Sensor-fingerprint matching (P6.B3, PRNU).

Every photosite has a fixed gain error, so a camera stamps a faint per-pixel
pattern into every frame it takes: the photo response non-uniformity. It is not
visible, it is the same in every shot from that body, and it is shared by no other
camera. Two frames from one camera therefore agree on a signal that frames from
different cameras do not.

The estimate is the standard two-stage construction (Fridrich et al.): a wavelet
residual `d = I - W(I)` keeps the finest detail band and discards scene structure,
and a fingerprint is `sum(d) / sum(I - W(I))` accumulated over several frames, so
the fixed pattern adds up while the content averages out. Correlation of a query
frame's residual against a reference fingerprint is then the match statistic.

**This scores whether two frames share a sensor, not whether they share a
person.** It is the weakest of the three image modules in what it can support, and
the payload says which. Two frames of the same person from two different phones
score as different cameras; two frames of different people from one phone score as
the same camera. Nothing here identifies anybody.

Three limits are load-bearing and stated in every result:

- **The reference must come from the same camera as the query.** A fingerprint
  built from other images of the *scene* rather than the *sensor* matches nothing.
  The payload carries `reference_count`, because a fingerprint from a single frame
  is not a fingerprint.
- **Correlation is dataset-specific.** No published operating point transfers to
  an unknown population, camera fleet and imaging pipeline. `THRESHOLD` is a
  screening value measured on this module's own fixtures, not a calibrated one, and
  the payload says so. A real deployment needs a ROC built from the cameras it will
  actually see.
- **In-camera processing erodes PRNU.** Demosaicing, sharpening and noise reduction
  all attack it, and modern cameras apply all three. A fingerprint from a JPEG is
  weaker than one from a raw frame.

Determinism: the wavelet is a fixed orthonormal Haar transform, so the residual is
a pure function of the pixels. No randomness, no wall clock.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
from PIL import Image

from app.analysis.types import (
    AnalysisNotApplicable,
    ModuleSpec,
    Tier,
    build_result,
)

ALGORITHM_VERSION = "1.0.0"

TIER = Tier.B

#: Screening value for correlation against a reference fingerprint. Measured on the
#: synthetic fixtures in tests/fixtures_images.py, which have a known fingerprint.
#: NOT a calibrated threshold: see the module docstring.
THRESHOLD = 0.20

#: Frames needed before an estimate means anything. One frame gives a residual, not
#: a fingerprint.
MIN_REFERENCE_IMAGES = 2

#: Below this, 8-bit quantisation noise swamps the signal and correlation is noise.
MIN_PIXELS = 4096

DISCLAIMER = (
    "Camera-fingerprint correlation: it indicates whether two frames came from "
    "the same sensor body. It says NOTHING about the people in them. Two people "
    "shot on one camera correlate as a match; one person shot on two cameras does "
    "not. The correlation threshold is a screening value measured on synthetic "
    "test fixtures, not an operating point calibrated for this deployment. "
    "In-camera processing erodes fingerprints, so a low correlation is not "
    "evidence that a different camera took the frame."
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "not person identification: it links images to a camera body, never to a " "person",
    "the threshold is a screening value from synthetic fixtures, not calibrated "
    "for any real camera population",
    "demosaicing, in-camera denoising and sharpening all reduce PRNU; modern "
    "cameras apply all three, and phone cameras apply them hardest",
    "requires several reference frames from the same camera; a single frame is a "
    "residual, not a fingerprint",
    "a low correlation is absence of signal, not proof that a different camera " "was used",
    "identical or near-identical frames are trivially correlated and say nothing "
    "about the sensor",
)


def _versions() -> str:
    from PIL import __version__ as PILLOW_VERSION

    return f"pil{PILLOW_VERSION}"


VERSION = f"{ALGORITHM_VERSION}+haar1+{_versions()}"


def _decode(data: bytes) -> np.ndarray:
    """Grayscale float64.

    Colour is discarded deliberately. The wavelet runs per channel and the three
    results would need combining, and a photosite's gain error is a property of the
    site rather than of the colour filter over it, so the luminance channel carries
    the same information with less noise to average around it.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 - undecodable input is a refusal
        raise AnalysisNotApplicable(
            f"PRNU matching cannot be applied: the evidence did not decode as an " f"image ({exc})"
        ) from exc

    if image.width * image.height < MIN_PIXELS:
        raise AnalysisNotApplicable(
            f"image is {image.width}x{image.height}; below the {MIN_PIXELS}px floor "
            f"where quantisation noise stops dominating the residual"
        )
    grey = np.asarray(image.convert("L"), dtype=np.float64)
    result: np.ndarray = grey
    return result


def _haar_step(band: np.ndarray) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    """One orthonormal Haar level: (approximation, three detail bands).

    Odd dimensions are trimmed rather than padded, so the output is exactly half
    the input in each axis and the transform stays area-conserving.
    """
    height, width = band.shape
    trimmed = band[: height - height % 2, : width - width % 2]
    blocks = trimmed.reshape(trimmed.shape[0] // 2, 2, trimmed.shape[1] // 2, 2)
    a00 = blocks[:, 0, :, 0]
    a01 = blocks[:, 0, :, 1]
    a10 = blocks[:, 1, :, 0]
    a11 = blocks[:, 1, :, 1]
    approximation = (a00 + a01 + a10 + a11) / 2.0
    details = (
        (a00 - a01 + a10 - a11) / 2.0,
        (a00 + a01 - a10 - a11) / 2.0,
        (a00 - a01 - a10 + a11) / 2.0,
    )
    return approximation, details


def haar_detail(band: np.ndarray) -> np.ndarray:
    """Finest-level horizontal detail: the band that carries sensor grain.

    Fixed and exact, so the residual is a pure function of the pixels — the
    property that makes the result reproducible at all.
    """
    _, (horizontal, _, _) = _haar_step(band)
    return horizontal


def residual(image: np.ndarray) -> np.ndarray:
    """The wavelet residual `d` for one frame.

    Mean-centred. A raw Haar detail band is already DC-free for natural content,
    but a perfectly linear ramp gives a constant detail of -1 rather than zero —
    so a synthetic or heavily processed frame can carry a nonzero mean, and that
    mean is a brightness term, not a sensor signature. `correlate` centres anyway;
    this keeps the estimator honest before it gets there.
    """
    detail = haar_detail(image)
    centred: np.ndarray = detail - detail.mean()
    return centred


def estimate_fingerprint(images: list[np.ndarray]) -> np.ndarray:
    """Accumulate residuals over frames into a single fingerprint estimate.

    Plain mean of the residuals. The literature's weighted form divides by the local
    intensity to cope with saturated regions; that correction assumes an imaging
    pipeline this module has not seen, so the unweighted form is used and the
    limitation is recorded rather than approximated.
    """
    stacked = np.stack([residual(image) for image in images], axis=0)
    estimate: np.ndarray = stacked.mean(axis=0)
    return estimate


def correlate(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation, the standard PRNU match statistic.

    Both vectors are mean-centred first, so a difference in overall brightness
    between two frames does not register as a fingerprint match.
    """
    x = a.ravel() - a.mean()
    y = b.ravel() - b.mean()
    denominator = float(np.linalg.norm(x) * np.linalg.norm(y))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(x, y) / denominator)


def run(
    data: bytes,
    params: dict[str, Any],
    *,
    reference_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Correlate this frame's residual against a reference fingerprint.

    The reference arrives as `reference_evidence_id` (resolved case-scoped by the
    executor), because a fingerprint is only meaningful when the operator asserts
    the reference frames came from one camera. That assertion is a human judgement
    and the payload records it as such rather than inferring it.
    """
    if reference_bytes is None:
        raise AnalysisNotApplicable(
            "PRNU matching needs a reference image from the same camera; set "
            "params.reference_evidence_id"
        )

    query = _decode(data)
    reference = _decode(reference_bytes)

    fingerprint = estimate_fingerprint([reference])
    correlation = correlate(fingerprint, residual(query))

    # A single reference frame gives a residual, not a fingerprint. Say so rather
    # than reporting a correlation from it as though it were a match statistic.
    reference_count = 1
    if "reference_image_ids" in params:
        ids = params["reference_image_ids"]
        if isinstance(ids, list) and ids:
            reference_count = len(ids) + 1

    spec = ModuleSpec(
        name="image-prnu",
        version=VERSION,
        tier=TIER,
        threshold=THRESHOLD,
        accepts=("image/png", "image/tiff"),
        disclaimer=DISCLAIMER,
    )

    matched = correlation >= THRESHOLD
    detail: dict[str, Any] = {
        "score_is": (
            "Pearson correlation between this frame's wavelet residual and the "
            "reference fingerprint; it measures shared sensor, not shared subject"
        ),
        "correlation": round(correlation, 6),
        "reference_count": reference_count,
        "reference_is_single_frame": reference_count < MIN_REFERENCE_IMAGES,
        "wavelet": "Haar, finest-level horizontal detail",
        "colour": "luminance only",
        "calibration": {
            "threshold_source": "screening value measured on synthetic fixtures "
            "in this repository",
            "calibrated_on_this_deployment": False,
            "note": "correlation thresholds are specific to a camera population "
            "and imaging pipeline; no published operating point transfers to an "
            "unknown one",
        },
        "known_limitations": list(KNOWN_LIMITATIONS),
        "params": params,
        "interpretation": (
            "correlation above the screening value: these frames share a sensor "
            "fingerprint. This is not evidence that they show the same person."
            if matched
            else "correlation below the screening value: no shared fingerprint "
            "was found. In-camera processing can erase one, so this is not proof "
            "that a different camera was used."
        ),
    }
    return build_result(spec, score=correlation, detail=detail)


spec = ModuleSpec(
    name="image-prnu",
    version=VERSION,
    tier=TIER,
    threshold=THRESHOLD,
    accepts=("image/png", "image/tiff"),
    params={
        "reference_evidence_id": {
            "type": "str",
            "required": True,
            "description": "frame from the same camera to compare against",
        },
        "reference_image_ids": {
            "type": "list[str] | null",
            "default": None,
            "description": "further reference frames, when several are available",
        },
    },
    requires_gpu=False,
    disclaimer=DISCLAIMER,
)


class _PrnuModule:
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
        # Pure algorithm: no weights, no auxiliary model.
        _ = model_bytes, aux_models
        return run(data, params, reference_bytes=reference_bytes)


module = _PrnuModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

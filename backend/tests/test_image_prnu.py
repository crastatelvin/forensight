"""PRNU sensor-fingerprint tests (P6.B3).

The fixtures carry a *known* fingerprint, so these tests can assert that the
estimator recovers something that is actually there — not merely that two arrays
correlate. The negative control uses the same scene with a different fingerprint,
which isolates the sensor as the only variable.

The honest caveat, repeated here because it bounds everything below: the fixtures
are synthetic. They show the estimator behaves as the literature describes; they
say nothing about a real sensor, whose PRNU is degraded by demosaicing and
in-camera denoising in ways no fixture reproduces.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.analysis.modules import image_prnu
from app.analysis.types import AnalysisNotApplicable, Tier

from .fixtures_images import make_garbage, make_prnu_image

#: Same sensor, unseen scene. This is the query frame: a held-out capture from a
#: camera whose fingerprint was estimated from other frames of a different scene.
QUERY_SCENE = 50


def _run(query_subject: int, ref_subject: int, **kwargs: Any) -> dict[str, Any]:
    return image_prnu.run(
        make_prnu_image(query_subject, scene=QUERY_SCENE),
        {},
        reference_bytes=make_prnu_image(ref_subject, scene=0),
        **kwargs,
    )


# --- The core property ------------------------------------------------------


def test_same_sensor_correlates_above_threshold() -> None:
    result = _run(query_subject=0, ref_subject=0)
    assert result["score"] >= result["threshold"], (
        f"same sensor scored {result['score']:.4f} against threshold " f"{result['threshold']}"
    )


@pytest.mark.parametrize("other", [1, 2, 3])
def test_different_sensor_correlates_below_threshold(other: int) -> None:
    result = _run(query_subject=other, ref_subject=0)
    assert result["score"] < result["threshold"], (
        f"different sensors scored {result['score']:.4f}, at or above threshold "
        f"{result['threshold']}"
    )


def test_populations_separate() -> None:
    """Every same-sensor pair must outrank every different-sensor pair.

    This is the property the threshold exists to express. If the sets ever overlap,
    no threshold is valid and the module should not be offering one.
    """
    same = [_run(0, 0)["score"] for _ in range(3)]
    different = [_run(other, 0)["score"] for other in (1, 2, 3)]
    assert min(same) > max(different), (
        f"populations overlap: same-sensor min {min(same):.4f} is not above "
        f"different-sensor max {max(different):.4f}"
    )


def test_golden_correlation() -> None:
    """Pinned. Movement means the estimator or the fixture changed."""
    result = _run(query_subject=0, ref_subject=0)
    assert result["score"] == pytest.approx(
        0.2790, abs=5e-3
    ), f"correlation moved to {result['score']:.4f}"


# --- The honesty of the claim ------------------------------------------------


def test_result_does_not_claim_person_identification() -> None:
    """The single most important thing this module must never imply."""
    result = _run(0, 0)
    detail = result["detail"]
    assert "share a sensor fingerprint" in detail["interpretation"]
    assert "not evidence that they show the same person" in detail["interpretation"]
    assert any("not person identification" in item for item in detail["known_limitations"])


def test_below_threshold_is_not_proof_of_a_different_camera() -> None:
    """In-camera processing erases fingerprints, so absence of signal is not signal
    of absence."""
    detail = _run(1, 0)["detail"]
    assert (
        "not proof" in detail["interpretation"]
        or "In-camera processing" in detail["interpretation"]
    )
    assert any("absence of signal, not proof" in item for item in detail["known_limitations"])


def test_threshold_is_declared_uncalibrated() -> None:
    detail = _run(0, 0)["detail"]
    assert detail["calibration"]["calibrated_on_this_deployment"] is False
    assert "screening" in detail["calibration"]["threshold_source"]


def test_single_reference_frame_is_flagged() -> None:
    """One frame is a residual, not a fingerprint. Reporting a correlation from it
    as a match statistic would overstate what was measured."""
    detail = _run(0, 0)["detail"]
    assert detail["reference_count"] == 1
    assert detail["reference_is_single_frame"] is True


def test_multiple_reference_frames_are_counted() -> None:
    result = image_prnu.run(
        make_prnu_image(0, scene=QUERY_SCENE),
        {"reference_image_ids": ["a", "b", "c"]},
        reference_bytes=make_prnu_image(0, scene=0),
    )
    detail = result["detail"]
    assert detail["reference_count"] == 4
    assert detail["reference_is_single_frame"] is False


# --- Refusals ----------------------------------------------------------------


def test_missing_reference_refuses() -> None:
    with pytest.raises(AnalysisNotApplicable) as exc:
        image_prnu.run(make_prnu_image(0), {})
    assert "same camera" in str(exc.value)


def test_undecodable_input_refuses() -> None:
    with pytest.raises(AnalysisNotApplicable):
        image_prnu.run(make_garbage(), {}, reference_bytes=make_prnu_image(0))


def test_tiny_image_refuses() -> None:
    """Below MIN_PIXELS, quantisation noise dominates and correlation is noise."""
    tiny = make_prnu_image(0, width=16, height=16)
    with pytest.raises(AnalysisNotApplicable) as exc:
        image_prnu.run(tiny, {}, reference_bytes=tiny)
    assert "quantisation" in str(exc.value)


# --- T6.7 --------------------------------------------------------------------


def test_result_carries_threshold_and_version() -> None:
    result = _run(0, 0)
    assert result["threshold"] == image_prnu.THRESHOLD
    assert result["model_version"] == image_prnu.VERSION
    assert result["tier"] == Tier.B.value
    assert "haar" in result["model_version"]


# --- T6.5 --------------------------------------------------------------------


def test_repeated_runs_are_identical() -> None:
    first = _run(0, 0)
    for _ in range(4):
        assert _run(0, 0) == first


def test_fixture_generation_is_deterministic() -> None:
    assert make_prnu_image(0, scene=3) == make_prnu_image(0, scene=3)


# --- Pure helpers, no reference needed ---------------------------------------


def test_haar_is_an_orthonormal_transform() -> None:
    """Scaling matters: a non-orthonormal detail filter would scale the residual
    differently on each frame and bias the correlation between differently-exposed
    shots.

    Checked on the full 2-D transform. The property that matters is energy
    conservation: an orthonormal Haar step splits the signal into four bands whose
    energies sum back to the original exactly. (A quarter each, not a half -- an
    earlier version of this assertion had half and was simply wrong.)
    """
    import numpy as np

    image = np.arange(32 * 32, dtype=np.float64).reshape(32, 32) % 17.0
    approx, (horizontal, vertical, diagonal) = image_prnu._haar_step(image)

    total = float(np.linalg.norm(image.ravel())) ** 2
    approx_energy = float(np.linalg.norm(approx.ravel())) ** 2
    detail_energy = sum(
        float(np.linalg.norm(band.ravel())) ** 2 for band in (horizontal, vertical, diagonal)
    )

    # Conservation is the whole claim. The split between bands is *not* uniform —
    # it depends on where the signal's energy lives, so a smooth or periodic test
    # image puts almost everything in the approximation band. Two earlier versions
    # of this test asserted an even split (half, then quarters) and both were
    # simply wrong about what orthonormality implies.
    assert approx_energy + detail_energy == pytest.approx(total, rel=1e-12)

    # And the map is linear and injective: equal input means equal output, and a
    # zero input maps to zero. Together with conservation this is what makes the
    # residual a pure function of the pixels.
    _, again = image_prnu._haar_step(image)
    assert np.array_equal(again[0], horizontal), "the transform is not deterministic"
    zero_approx, zero_details = image_prnu._haar_step(np.zeros((8, 8)))
    assert np.allclose(zero_approx, 0.0)
    assert all(np.allclose(band, 0.0) for band in zero_details)


def test_residual_is_mean_free() -> None:
    """A pure wavelet detail band carries no DC component, so the estimator is not
    just measuring overall brightness."""
    import numpy as np

    img = np.full((64, 64), 120.0)
    assert np.allclose(image_prnu.residual(img), 0.0, atol=1e-9)

    ramp = np.tile(np.arange(64, dtype=np.float64), (64, 1))
    assert abs(float(image_prnu.residual(ramp).mean())) < 1e-9


def test_correlation_is_invariant_to_brightness() -> None:
    """Two frames of one camera at different exposures must still match.

    A statistic that moved with overall brightness would be measuring exposure,
    not the sensor."""
    import numpy as np

    rng_free = np.tile(np.arange(32, dtype=np.float64), (32, 1)) * 0.1
    rng_free = rng_free + np.roll(rng_free, 3, axis=1)
    bright = image_prnu.correlate(rng_free, rng_free)
    darker = image_prnu.correlate(rng_free, rng_free + 50.0)
    assert bright == pytest.approx(darker, abs=1e-9)


def test_correlation_of_constant_vectors_is_zero_not_nan() -> None:
    import numpy as np

    assert image_prnu.correlate(np.ones((8, 8)), np.ones((8, 8))) == 0.0


def test_fingerprint_average_reduces_scene_content() -> None:
    """The reason a fingerprint is an average: scene content differs per frame and
    cancels, the sensor pattern does not."""
    import io

    import numpy as np
    from PIL import Image

    from .fixtures_images import make_prnu_image as fixture

    frames = [
        np.asarray(Image.open(io.BytesIO(fixture(0, scene=k))).convert("L"), dtype=np.float64)
        for k in range(6)
    ]
    single = image_prnu.estimate_fingerprint(frames[:1])
    averaged = image_prnu.estimate_fingerprint(frames)

    def energy(x: np.ndarray) -> float:
        return float(np.linalg.norm(x.ravel() - x.mean()))

    # Averaging must not amplify noise; the scene term shrinks faster than the
    # sensor term, so the estimate gets *cleaner* even though its total energy drops.
    assert energy(averaged) < energy(single)

"""FI-2 — `near-duplicate`: golden tests, including the ceiling it has.

The measurement that decided the design: over the eight fixtures in `tests/assets`,
unrelated images sit 25–38 dHash bits apart and the *same* picture under a
re-encode, resize or small brightness change sits 0–3. The gap is real.

The second measurement is the one that matters more, and it is a limit rather than a
result. Crop tolerance, worst same-image fixture: 5% → 14 bits, 10% → 24, 15% → 30.
Against unrelated images whose *minimum* is 25, the ranges stop separating between
5% and 10%. These tests pin that ceiling, because a threshold whose limit an operator
cannot see is worse than no threshold — and because a future change that quietly
widens the crops would otherwise make this module look better than it is.
"""

from __future__ import annotations

import io
import itertools
import pathlib

import pytest
from app.analysis.modules import near_duplicate as nd
from app.analysis.types import AnalysisNotApplicable
from PIL import Image, ImageEnhance

ASSETS = pathlib.Path(__file__).parent / "assets"
FIXTURES = sorted(ASSETS.glob("*.jpg"))

#: Deterministic transforms. Pillow is pinned in `pyproject.toml`, so these produce
#: the same bytes on every run and the pinned distances below hold — which is why the
#: variants are *derived in the test* rather than committed. Committing re-encoded
#: copies would mean a second copy of each fixture to keep in sync for no gain.
VARIANTS = ("reencode_q60", "resize_50", "resize_75", "brightness_8", "crop_5", "crop_10")


def variant(data: bytes, kind: str) -> bytes:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    if kind == "reencode_q60":
        quality = 60
    elif kind == "resize_50":
        image = image.resize((image.width // 2, image.height // 2), Image.Resampling.LANCZOS)
        quality = 88
    elif kind == "resize_75":
        image = image.resize(
            (int(image.width * 0.75), int(image.height * 0.75)), Image.Resampling.LANCZOS
        )
        quality = 88
    elif kind == "brightness_8":
        image = ImageEnhance.Brightness(image).enhance(1.08)
        quality = 88
    elif kind in ("crop_5", "crop_10"):
        fraction = 0.05 if kind == "crop_5" else 0.10
        dx, dy = int(image.width * fraction), int(image.height * fraction)
        image = image.crop((dx, dy, image.width - dx, image.height - dy))
        quality = 88
    else:  # pragma: no cover - the list above is the contract
        raise AssertionError(f"unknown variant {kind}")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def distance(a: bytes, b: bytes) -> int:
    return nd.hamming(nd.difference_hash(a), nd.difference_hash(b))


# --- the fixtures are what the measurements assume ------------------------


def test_there_are_enough_fixtures_to_measure_with() -> None:
    """Eight images gives 28 unrelated pairs. Fewer and the claim is not a claim."""
    assert len(FIXTURES) == 8
    assert len(list(itertools.combinations(FIXTURES, 2))) == 28
    # Distinct content, or every "unrelated" pair is secretly a duplicate.
    assert len({p.read_bytes() for p in FIXTURES}) == 8


# --- the positive result, pinned -------------------------------------------


@pytest.mark.parametrize(
    "kind", ["reencode_q60", "resize_50", "resize_75", "brightness_8", "crop_5"]
)
def test_a_transformed_copy_stays_inside_the_threshold(kind: str) -> None:
    """Resizing, re-encoding, brightening and a 5% crop all still match."""
    distances = [distance(p.read_bytes(), variant(p.read_bytes(), kind)) for p in FIXTURES]
    assert max(distances) <= nd.THRESHOLD_DISTANCE, (
        f"{kind} pushed a same-image pair to {max(distances)} bits, past the "
        f"{nd.THRESHOLD_DISTANCE}-bit bar; the threshold is stale"
    )


@pytest.mark.parametrize(
    "kind", ["reencode_q60", "resize_50", "resize_75", "brightness_8", "crop_5"]
)
def test_the_measured_maxima_are_pinned(kind: str) -> None:
    """Exact distances, so a drift in Pillow or the transform is visible.

    Not because tight numbers are good — the point of the next test is that the
    module's discrimination is bounded — but because a silent drift in either would
    change what this reports without anything failing loudly.
    """
    maxima = {
        "reencode_q60": 1,
        "resize_50": 1,
        "resize_75": 1,
        "brightness_8": 3,
        "crop_5": 14,
    }
    distances = [distance(p.read_bytes(), variant(p.read_bytes(), kind)) for p in FIXTURES]
    assert max(distances) == maxima[kind]


def test_unrelated_images_are_all_beyond_the_threshold() -> None:
    """The separation the module exists for."""
    cross = [
        distance(a.read_bytes(), b.read_bytes()) for a, b in itertools.combinations(FIXTURES, 2)
    ]
    assert len(cross) == 28
    assert min(cross) == 25, "the unrelated baseline moved; every pinned number depends on it"
    assert min(cross) > nd.THRESHOLD_DISTANCE


# --- the ceiling, which is the part worth pinning --------------------------


def test_a_ten_percent_crop_is_past_where_this_module_can_separate() -> None:
    """The limit, asserted rather than left to be discovered in production.

    At a 10% centre-crop the worst same-image pair reaches 24 bits while the *best*
    unrelated pair sits at 25. The ranges touch, so no threshold separates them. This
    is why the payload refuses to call a high distance "unrelated" — the honest
    reading is "not shown to match".
    """
    cropped = [distance(p.read_bytes(), variant(p.read_bytes(), "crop_10")) for p in FIXTURES]
    cross = [
        distance(a.read_bytes(), b.read_bytes()) for a, b in itertools.combinations(FIXTURES, 2)
    ]

    assert max(cropped) == 24, "the 10% crop measurement moved; re-derive the ceiling"
    assert min(cross) == 25
    # Touching, not separated. Asserted as a non-property on purpose: if a future
    # change makes this separate, someone must update the module's stated tolerance
    # rather than have the limit quietly become wrong.
    assert max(cropped) >= min(cross) - 1


def test_the_module_says_it_cannot_rule_out_a_heavily_cropped_copy() -> None:
    """A "no" must not read as "no relationship"."""
    pair = (FIXTURES[0], variant(FIXTURES[0].read_bytes(), "crop_10"))
    result = nd.run(FIXTURES[0].read_bytes(), {}, reference_bytes=pair[1])

    interpretation = result["detail"]["interpretation"]
    assert "NOT mean they are unrelated" in interpretation
    assert "not shown to match" in interpretation
    assert any("10%" in limit for limit in result["detail"]["known_limitations"])


# --- the module's own contract --------------------------------------------


def test_the_score_is_the_complement_of_the_bit_distance() -> None:
    """The score is the documented quantity, not something adjacent to it."""
    a, b = FIXTURES[0].read_bytes(), variant(FIXTURES[0].read_bytes(), "resize_50")
    result = nd.run(a, {}, reference_bytes=b)
    expected = 1.0 - nd.hamming(nd.difference_hash(a), nd.difference_hash(b)) / 64
    assert result["score"] == pytest.approx(expected, abs=1e-12)
    assert 0.0 <= result["score"] <= 1.0
    assert result["detail"]["distance_bits"] == int(round((1 - expected) * 64))


def test_repeated_runs_are_identical() -> None:
    """Admissibility depends on reproducing a score, and there are no weights."""
    a, b = FIXTURES[0].read_bytes(), variant(FIXTURES[0].read_bytes(), "reencode_q60")
    kwargs = {"reference_bytes": b}
    assert nd.run(a, {}, **kwargs) == nd.run(a, {}, **kwargs)


def test_a_missing_reference_refuses_rather_than_scoring() -> None:
    """One image alone is not a low similarity."""
    with pytest.raises(AnalysisNotApplicable, match="no reference image"):
        nd.run(FIXTURES[0].read_bytes(), {})
    with pytest.raises(AnalysisNotApplicable, match="reference_evidence_id"):
        nd.module(FIXTURES[0].read_bytes(), {})


def test_undecodable_input_refuses() -> None:
    """Bytes that are not an image are not a low similarity."""
    good = FIXTURES[0].read_bytes()
    with pytest.raises(AnalysisNotApplicable):
        nd.run(b"\xff\xfe\x00\x01 not an image", {}, reference_bytes=good)
    with pytest.raises(AnalysisNotApplicable):
        nd.run(good, {}, reference_bytes=b"\xff\xfe\x00\x01 not an image either")


def test_a_module_registers_without_any_weights() -> None:
    """The capability list must not send an operator looking for a download."""
    assert nd.spec.model_key is None
    assert nd.spec.model_sha256 is None
    assert nd.spec.aux_models == {}
    assert nd.spec.requires_gpu is False


def test_the_hash_is_64_bits_and_stable_under_a_no_op_resize() -> None:
    """The hash width is a contract, not an implementation detail."""
    hash_value = nd.difference_hash(FIXTURES[0].read_bytes())
    assert 0 <= hash_value < 2**64
    assert nd.hamming(hash_value, hash_value) == 0
    same = variant(FIXTURES[0].read_bytes(), "resize_75")
    assert nd.difference_hash(same) == hash_value


def test_the_result_refuses_the_readings_it_cannot_support() -> None:
    """Says what a match does not establish, and what it is not."""
    a, b = FIXTURES[0].read_bytes(), variant(FIXTURES[0].read_bytes(), "resize_50")
    result = nd.run(a, {"reference_evidence_id": "u", "reference_label": "copy"}, reference_bytes=b)

    assert "NOT proof that two files are the same photograph" in result["disclaimer"]
    assert result["tier"] == "B"
    assert result["detail"]["calibration"]["calibrated_on_this_deployment"] is False
    assert result["detail"]["reference_label"] == "copy"

    limitations = " ".join(result["detail"]["known_limitations"])
    assert "screenshot" in limitations
    assert "whether either image was manipulated" in limitations
    # The distinction from the other image module is stated in the payload, not left
    # to an operator to work out.
    assert "image-prnu" in result["detail"]["distinct_from_image_prnu"]
    # And the tolerance is published, so the threshold's limit is visible with it.
    assert "crops beyond roughly 10%" in " ".join(result["detail"]["does_not_tolerate"])


def test_the_result_carries_no_inline_reference_document() -> None:
    """Only the reference's identity travels into the payload.

    Result payloads are rendered into signed reports, so anything passed in params
    must not be echoed wholesale.
    """
    a = FIXTURES[0].read_bytes()
    result = nd.run(
        a,
        {"reference_evidence_id": "u", "reference_text": "an entire pasted document"},
        reference_bytes=variant(a, "resize_50"),
    )
    assert result["detail"]["params"]["reference_evidence_id"] == "u"
    assert "reference_text" not in result["detail"]["params"]

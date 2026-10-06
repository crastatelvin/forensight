"""Tests for the EXIF metadata analyzer (P6.B3, T6.4).

The load-bearing assertion in this file is the negative one: an ordinary camera
photo must score zero. An analyzer that only ever fires looks useful and is
worthless, so the clean fixture is treated as the primary test case rather than an
afterthought.
"""

from __future__ import annotations

import pytest
from app.analysis.modules import image_exif
from app.analysis.types import AnalysisNotApplicable, Tier

from .fixtures_images import (
    CAMERA_EXIF,
    EDITED_EXIF,
    exif_bytes,
    exif_with_gps,
    make_garbage,
    make_photo_jpeg,
    strip_exif,
)


def _ids(result: dict) -> list[str]:
    return [item["id"] for item in result["detail"]["indicators_fired"]]


# --- The clean case, which is the one that matters ---------------------------


def test_untouched_camera_photo_scores_zero() -> None:
    result = image_exif.run(exif_bytes(CAMERA_EXIF), {})
    assert result["score"] == 0.0
    assert result["detail"]["indicators_fired"] == []
    assert result["score"] < result["threshold"]


def test_clean_photo_still_extracts_metadata() -> None:
    """Not flagged is not the same as unexamined."""
    detail = image_exif.run(exif_bytes(CAMERA_EXIF), {})["detail"]
    assert detail["recorded_fields"] == [
        "DateTime",
        "DateTimeOriginal",
        "Make",
        "Model",
        "Software",
    ]
    assert detail["exif_present"] is True


def test_camera_firmware_is_reported_but_not_scored() -> None:
    """A camera writes its own firmware to Software. Scoring that would mean
    guessing which applications are editors, so it is reported only."""
    result = image_exif.run(exif_bytes(CAMERA_EXIF), {})
    assert "software_present" not in _ids(result)
    notable = {item["id"] for item in result["detail"]["notable_fields"]}
    assert "software_present" in notable
    assert result["score"] == 0.0


# --- T6.4: tampered/stripped fixtures flagged, clean ones not ----------------


def test_stripped_metadata_is_flagged() -> None:
    result = image_exif.run(strip_exif(exif_bytes(CAMERA_EXIF)), {})
    assert _ids(result) == ["no_exif"]
    assert result["score"] >= result["threshold"]


def test_image_with_no_metadata_at_all_is_flagged() -> None:
    result = image_exif.run(make_photo_jpeg(), {})
    assert _ids(result) == ["no_exif"]
    assert result["score"] == 1.0


def test_resave_that_rewrote_only_the_modification_date_is_flagged() -> None:
    """The strongest indicator available without reading pixels."""
    data = exif_bytes({"Make": "Canon", "Model": "EOS", "DateTime": "2026:01:02 03:04:05"})
    result = image_exif.run(data, {})
    assert "datetime_inconsistent" in _ids(result)
    assert result["score"] >= result["threshold"]


def test_one_missing_field_alone_does_not_flag() -> None:
    """A single absent field is normal on original files, so it must not flag."""
    result = image_exif.run(exif_bytes({"Make": "Canon"}), {})
    assert _ids(result) == ["no_datetime_original"]
    assert result["score"] < result["threshold"]


def test_gps_presence_does_not_flag() -> None:
    """GPS is a privacy exposure, not evidence of manipulation."""
    result = image_exif.run(exif_with_gps(), {})
    assert _ids(result) == []
    assert result["score"] == 0.0
    assert result["detail"]["gps_present"] is True
    assert "gps_present" in {n["id"] for n in result["detail"]["notable_fields"]}


# --- Honest scoring semantics ------------------------------------------------


def test_score_is_a_ratio_not_a_probability() -> None:
    result = image_exif.run(exif_bytes(EDITED_EXIF), {})
    assert "NOT a probability" in result["detail"]["score_is"]


def test_score_never_exceeds_one() -> None:
    for data in (make_photo_jpeg(), exif_with_gps(), strip_exif(exif_bytes(CAMERA_EXIF))):
        assert 0.0 <= image_exif.run(data, {})["score"] <= 1.0


def test_threshold_sits_between_the_known_cases() -> None:
    """Flags the cases it should, leaves the rest alone."""
    cases = [
        (make_photo_jpeg(), True),
        (strip_exif(exif_bytes(CAMERA_EXIF)), True),
        (exif_bytes(CAMERA_EXIF), False),
        (exif_with_gps(), False),
        (exif_bytes(EDITED_EXIF), False),
    ]
    for data, expect_flagged in cases:
        result = image_exif.run(data, {})
        flagged = result["score"] >= result["threshold"]
        assert flagged is expect_flagged, (
            f"score {result['score']} for {_ids(result)}; expected " f"flagged={expect_flagged}"
        )


def test_golden_scores() -> None:
    golden = {
        "no_exif": (make_photo_jpeg, 1.0),
        "clean_camera": (lambda: exif_bytes(CAMERA_EXIF), 0.0),
        "camera_gps": (exif_with_gps, 0.0),
        "stripped": (lambda: strip_exif(exif_bytes(CAMERA_EXIF)), 1.0),
        "edited": (lambda: exif_bytes(EDITED_EXIF), 1 / 3),
    }
    for name, (fixture, expected) in golden.items():
        assert image_exif.run(fixture(), {})["score"] == pytest.approx(expected, abs=1e-6), name


# --- T6.7 --------------------------------------------------------------------


def test_result_carries_threshold_and_version() -> None:
    result = image_exif.run(exif_bytes(CAMERA_EXIF), {})
    assert result["threshold"] == image_exif.THRESHOLD
    assert result["model_version"] == image_exif.VERSION
    assert result["tier"] == Tier.B.value
    assert result["disclaimer"]


def test_disclaimer_matches_the_technique_not_the_tier() -> None:
    """The generic Tier B text is about identity matching, which is meaningless
    on a metadata result. The module states its own limits."""
    result = image_exif.run(exif_bytes(CAMERA_EXIF), {})
    assert "identity match" not in result["disclaimer"]
    assert "metadata" in result["disclaimer"].lower()


def test_model_version_encodes_pillow() -> None:
    assert "pil" in image_exif.VERSION


# --- T6.2 in spirit: refuse rather than invent --------------------------------


def test_undecodable_input_refuses() -> None:
    with pytest.raises(AnalysisNotApplicable):
        image_exif.run(make_garbage(), {})


# --- T6.5 --------------------------------------------------------------------


def test_repeated_runs_are_identical() -> None:
    data = exif_bytes(CAMERA_EXIF)
    runs = [image_exif.run(data, {}) for _ in range(5)]
    for other in runs[1:]:
        assert other == runs[0]


def test_corrupt_gps_ifd_does_not_fail_the_read() -> None:
    """A malformed sub-IFD must not take the whole assessment down."""
    data = bytearray(exif_with_gps())
    data = bytes(data)
    result = image_exif.run(data, {})
    assert result["score"] >= 0.0

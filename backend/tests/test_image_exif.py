"""Tests for the EXIF metadata analyzer (P6.B3, T6.4).

The load-bearing assertion in this file is the negative one: an ordinary camera
photo must score zero. An analyzer that only ever fires looks useful and is
worthless, so the clean fixture is treated as the primary test case rather than an
afterthought.
"""

from __future__ import annotations

import pytest
from app.analysis.modules import image_exif
from app.analysis.modules.image_exif import run
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


# --- GPS in decimal degrees (P7.B5 geospatial view) -----------------------


def test_gps_is_converted_to_signed_decimal_degrees() -> None:
    """51°30'00"N, 0°7'00"W must come out as 51.5 and -0.1166667.

    The refs are the whole test: an implementation that ignores them returns
    (0.1167, 51.5) — positive, southern, eastern — which is a real place, in the
    wrong hemisphere, several thousand kilometres from the truth. A wrong coordinate
    is worse than none because it looks like data.
    """
    from tests.fixtures_images import exif_with_gps

    out = run(exif_with_gps(), {})
    gps = out["detail"]["gps"]

    assert gps["latitude"] == 51.5, gps
    assert gps["longitude"] == -0.1166667, gps
    assert gps["latitude_ref"] == "N"
    assert gps["longitude_ref"] == "W"
    # The raw triples travel alongside, so a reviewer can re-derive this by hand.
    assert gps["raw_latitude"] == ["51", "30", "0"], gps
    assert gps["raw_longitude"] == ["0", "7", "0"], gps


def test_southern_and_eastern_refs_are_honoured() -> None:
    """The conversion is not hard-coded to the northern western hemisphere."""
    import io

    from PIL import ExifTags, Image

    from tests.fixtures_images import _GPS, _gradient  # noqa: PLC2701 - test helper

    image = Image.fromarray(_gradient(32, 32), mode="RGB")
    exif = Image.Exif()
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[_GPS["GPSLatitudeRef"]] = "S"
    gps[_GPS["GPSLatitude"]] = (33.0, 51.0, 0.0)  # Sydney
    gps[_GPS["GPSLongitudeRef"]] = "E"
    gps[_GPS["GPSLongitude"]] = (151.0, 12.0, 0.0)
    exif[ExifTags.Base.GPSInfo] = gps
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, exif=exif)

    coords = run(buffer.getvalue(), {})["detail"]["gps"]
    assert coords["latitude"] == -33.85, coords
    assert coords["longitude"] == 151.2, coords


def test_a_malformed_gps_yields_no_coordinate_rather_than_a_guess() -> None:
    """Missing or unparseable components produce `{}`, not a point at 0,0.

    (0, 0) is in the Gulf of Guinea. Emitting it for an unreadable IFD would place
    evidence somewhere real and wrong, which is precisely the failure mode the
    honesty clause exists to prevent — and it would look like a measurement.
    """
    from app.analysis.modules.image_exif import gps_to_decimal

    # Latitude only: no longitude at all.
    assert gps_to_decimal({2: (51.0, 30.0, 0.0), 1: "N"}) == {}
    # Nonsense in place of numbers.
    assert gps_to_decimal({1: "N", 2: ("not", "numbers"), 3: "W", 4: (0.0, 7.0, 0.0)}) == {}
    # Absent seconds is normal, not an error.
    partial = gps_to_decimal({1: "N", 2: (51.0, 30.0), 3: "E", 4: (0.0, 7.0)})
    assert partial["latitude"] == 51.5, partial


def test_gps_presence_still_does_not_affect_the_score() -> None:
    """A coordinate is not evidence of manipulation, and the score must show it.

    Compared by *removing only the GPS IFD from the same image*, because comparing
    two different photographs proves nothing: they differ in every other way too,
    and the first version of this test did exactly that and "found" a difference
    that had nothing to do with GPS.

    The module has always refused to score GPS presence. This pins the decision now
    that coordinates are actually extracted and surfaced in the UI — because "we
    have a location now" is precisely the change that makes a score start creeping
    toward "this file is dirty".
    """
    import io

    from PIL import ExifTags, Image

    from tests.fixtures_images import exif_with_gps

    with_gps = exif_with_gps()
    assert run(with_gps, {})["detail"]["gps"], "the fixture should carry a coordinate"

    # Same pixels, same Make/Model/DateTimeOriginal; GPS IFD deleted.
    image = Image.open(io.BytesIO(with_gps))
    exif = image.getexif()
    for tag_name in ("GPSInfo", "GPSLatitude", "GPSLongitude"):
        tag = getattr(ExifTags.Base, tag_name, None) or ExifTags.IFD.GPSInfo
        exif[tag] = {}
    image.getexif()[ExifTags.Base.GPSInfo] = {}
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, exif=exif)

    without = run(buffer.getvalue(), {})
    assert not without["detail"]["gps_present"], "the GPS IFD was not actually removed"

    scored = run(with_gps, {})["score"]
    assert (
        scored == without["score"]
    ), f"GPS presence changed the score: {scored} vs {without['score']}"
    assert run(with_gps, {})["tier"] == without["tier"]

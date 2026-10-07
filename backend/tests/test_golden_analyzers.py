"""X5 — golden tests for every analyzer, on generated fixtures, with no simulation.

A golden test pins the exact output of an analyzer on a fixed input. That is a
stronger claim than "it runs and returns a number": it says the number is *this*
number, so a silent change in a library, a threshold, or the algorithm itself fails
the build rather than quietly altering what a signed report attests to.

**Exact floats are the point, not an inconvenience.** Admissibility depends on being
able to reproduce a result, and the analyzer's `model_version` already records the
library versions it was produced with. When those change, every score may change
with them — so a bump should be a deliberate act with the diff reviewed, and these
tests are what make it visible. A golden test loosened to `pytest.approx` would
record nothing worth recording.

The fixtures are generated rather than committed. A committed binary is opaque: the
test asserts on a file's history rather than on what it contains, and it cannot be
reviewed in a diff. `tests/fixtures_images.py` builds each one from a documented
recipe, so a golden value can be traced to the input that produced it.

**No simulation.** Every analyzer here runs its real code on real bytes. An analyzer
that is mocked in its own test is not tested.
"""

from __future__ import annotations

import hashlib
import os
import pathlib

import pytest
from app.analysis.modules import entropy, geo_correlate, image_ela, image_exif
from app.analysis.types import AnalysisNotApplicable

from tests.fixtures_images import (
    CAPTURED,
    MODIFIED,
    exif_at,
    exif_with,
    exif_with_gps,
    make_clean_jpeg,
    make_double_compressed_jpeg,
    make_empty_scene,
    make_garbage,
    make_photo_jpeg,
    make_tampered_jpeg,
    make_tiny_jpeg,
    strip_exif,
)


def fired(result: dict) -> list[str]:
    """The ids of the indicators that fired, in the module's own order."""
    return [i["id"] for i in result["detail"]["indicators_fired"]]


# --- byte-entropy (Tier B) ------------------------------------------------


def test_entropy_of_a_photograph_is_pinned() -> None:
    result = entropy.run(make_photo_jpeg(), {})

    assert result["score"] == 7.49666023973277
    assert result["tier"] == "B"
    assert result["threshold"] == 7.5
    assert result["model_version"] == "1.0.0"
    assert result["detail"]["bytes_analyzed"] == len(make_photo_jpeg())


def test_entropy_of_incompressible_bytes_is_exactly_eight() -> None:
    """The upper bound is a mathematical fact, not a measurement, so it is exact.

    Random bytes carry the maximum entropy a byte can hold. `make_garbage` is seeded,
    so this is reproducible; if it ever came out below 8.0 the generator had stopped
    being random and every other entropy value in this file would be suspect.
    """
    result = entropy.run(make_garbage(), {})
    assert result["score"] == 8.0


def test_entropy_of_a_flat_scene_is_low_and_pinned() -> None:
    """The other end of the scale, so the value is a measurement and not an artefact.

    A near-uniform scene has almost no structure to encode, which is why the
    threshold is set where it is: this is the case a high-entropy flag must not
    fire on.
    """
    result = entropy.run(make_empty_scene(), {})
    assert result["score"] == 3.4648095496869704
    assert result["score"] < result["threshold"], "a flat scene must not look tampered"


# --- image-ela (Tier A) ---------------------------------------------------


def test_ela_of_an_untouched_image_is_zero() -> None:
    """The floor of the scale: an image saved exactly once has no resave history."""
    assert image_ela.run(make_clean_jpeg(), {})["score"] == 0.0


def test_ela_ranks_clean_below_single_save_below_tampered() -> None:
    """The ordering is the claim; the exact values are pinned separately.

    A forensic score that does not increase with the manipulation it measures is
    worse than no score, because it looks authoritative. This asserts the ordering
    holds regardless of what the individual numbers become, so a library bump that
    rescales the whole scale cannot quietly invert it.
    """
    clean = image_ela.run(make_clean_jpeg(), {})["score"]
    single = image_ela.run(make_photo_jpeg(), {})["score"]
    tampered = image_ela.run(make_tampered_jpeg(), {})["score"]

    assert clean < single < tampered, (
        f"ELA must rank clean ({clean}) below single-save ({single}) below "
        f"tampered ({tampered})"
    )
    assert tampered > image_ela.THRESHOLD, (
        "the tampered fixture must exceed the threshold, or the module would never "
        "raise a lead on its own test material"
    )


def test_ela_scores_are_pinned() -> None:
    """The exact values behind that ordering.

    `ela_quality` is pinned too because it selects the JPEG quantization table the
    score is computed against — a change there changes every score silently.
    """
    single = image_ela.run(make_photo_jpeg(), {})
    assert single["score"] == 0.008203125
    assert single["detail"]["ela_quality"] == 75
    assert single["detail"]["anomalous_pixels"] == 630

    tampered = image_ela.run(make_tampered_jpeg(), {})
    assert tampered["score"] == 0.0504913330078125
    assert tampered["detail"]["anomalous_pixels"] == 3309


def test_ela_refuses_a_png_rather_than_scoring_it_zero() -> None:
    """A refusal, not a zero. This is the distinction the project is built on.

    ELA is defined on JPEG re-compression. Scoring a PNG would produce a number
    with no meaning, and a number is what a reader takes away.
    """
    from tests.fixtures_images import make_png

    with pytest.raises(AnalysisNotApplicable) as caught:
        image_ela.run(make_png(), {})
    assert "JPEG" in str(caught.value) or "re-compression" in str(caught.value)


def test_ela_refuses_an_image_too_small_to_be_meaningful() -> None:
    """A minimum pixel count, stated as a refusal rather than a low score."""
    with pytest.raises(AnalysisNotApplicable) as caught:
        image_ela.run(make_tiny_jpeg(), {})
    assert "4096" in str(caught.value)


def test_ela_double_compression_fixture_stays_flat() -> None:
    """Pinned because it surprised us and should keep being checked.

    A double-compressed image scores *lower* than a single-saved one here, which
    looks backwards for a manipulation signal. It is not: ELA measures anomalous
    error-level content, and re-encoding a smooth synthetic gradient produces
    almost none. The fixture is a gradient, not a photograph, so it exercises the
    decode path rather than the detection path — and pinning the value stops anyone
    later reading it as evidence that double compression is undetectable.
    """
    result = image_ela.run(make_double_compressed_jpeg(), {})
    assert result["score"] == 0.0
    assert result["detail"]["anomalous_pixels"] == 0


# --- image-exif (Tier B) --------------------------------------------------


def test_exif_with_no_metadata_at_all_reports_exactly_that() -> None:
    """One indicator, not four, when there is nothing to evaluate.

    With no metadata the assessment is a single fact, and reporting three further
    "missing" indicators alongside it would read as though more were checked.
    """
    result = image_exif.run(make_photo_jpeg(), {})
    assert fired(result) == ["no_exif"]
    assert result["score"] == 1.0


def test_exif_indicators_each_fire_in_isolation() -> None:
    """One fixture per indicator, so each is genuinely exercised.

    Before these fixtures existed every EXIF case in the suite tripped only
    `no_exif`, because each one either had no metadata or was stripped down from
    something that had none. The three indicators that actually describe
    provenance — a missing capture timestamp, an unidentified camera, and a
    modification date with no capture date — were dead code as far as any test was
    concerned.
    """
    assert fired(image_exif.run(exif_with(Make="Canon", Model="EOS 5D"), {})) == [
        "no_datetime_original"
    ]
    assert fired(image_exif.run(exif_with(DateTimeOriginal=CAPTURED), {})) == [
        "no_camera_identification"
    ]
    assert fired(
        image_exif.run(exif_with(Make="Canon", Model="EOS 5D", DateTime=MODIFIED), {})
    ) == ["no_datetime_original", "datetime_inconsistent"]


def test_exif_with_complete_metadata_fires_nothing() -> None:
    """The negative case, and the only one that shows the module is not simply
    reporting a constant. A score of 0.0 here is a measurement, not a default."""
    result = image_exif.run(exif_with(Make="Canon", Model="EOS 5D", DateTimeOriginal=CAPTURED), {})
    assert fired(result) == []
    assert result["score"] == 0.0


def test_exif_scores_are_the_fired_fraction() -> None:
    """Pins the arithmetic, so a change to the denominator is visible."""
    assert image_exif.run(make_photo_jpeg(), {})["score"] == 1.0
    assert image_exif.run(exif_with(Make="Canon", Model="EOS 5D"), {})["score"] == (1 / 3)
    assert image_exif.run(exif_with(Make="Canon", Model="EOS 5D", DateTime=MODIFIED), {})[
        "score"
    ] == (2 / 3)


def test_exif_gps_is_reported_without_being_scored() -> None:
    """A coordinate is not evidence of manipulation, and the score must show it.

    The fixture carries a full EXIF block plus GPS, so the only difference between
    it and the clean case is the coordinate. If GPS ever started contributing, this
    would be the test that caught it.
    """
    with_gps = image_exif.run(exif_with_gps(), {})
    assert with_gps["detail"]["gps"]["latitude"] == 51.5
    assert fired(with_gps) == [], "a GPS fixture with full metadata should be clean"


def test_stripping_metadata_from_a_stripped_file_changes_nothing() -> None:
    """Pinned because it looks like a missing test and is not.

    `strip_exif` on a fixture that never had EXIF is a no-op, so the score is
    identical. An earlier draft of this file treated that as a bug in the module and
    "fixed" the fixture; it was the test that was wrong.
    """
    assert (
        image_exif.run(strip_exif(make_photo_jpeg()), {})["score"]
        == image_exif.run(make_photo_jpeg(), {})["score"]
    )


# --- image-prnu (Tier B) --------------------------------------------------


def test_prnu_refuses_without_a_reference_image() -> None:
    """PRNU is a comparison, so one image is not an input — it is a refusal."""
    from tests.fixtures_images import make_prnu_image

    with pytest.raises(AnalysisNotApplicable) as caught:
        from app.analysis.modules import image_prnu

        image_prnu.run(make_prnu_image(), {})
    assert "reference" in str(caught.value).lower()


# --- model-backed analyzers ----------------------------------------------
#
# Gated on the same artifact paths the modules' own tests use, so the skip reason
# names the file to fetch. These two are the only golden assertions that do not run
# on a bare checkout, and they say so rather than passing silently.


def _yolo_weights() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get(
            "FORENSIGHT_YOLO_WEIGHTS",
            str(pathlib.Path(__file__).resolve().parent.parent / ".models" / "yolov4.onnx"),
        )
    )


def _face_weights() -> tuple[pathlib.Path, pathlib.Path]:
    model_dir = pathlib.Path(__file__).resolve().parent.parent / ".models"
    return (
        pathlib.Path(os.environ.get("FORENSIGHT_FACE_DETECTOR", str(model_dir / "yunet.onnx"))),
        pathlib.Path(os.environ.get("FORENSIGHT_FACE_EMBEDDER", str(model_dir / "sface.onnx"))),
    )


needs_yolo = pytest.mark.skipif(
    not _yolo_weights().exists(),
    reason=f"pinned weights absent at {_yolo_weights()}; run scripts/fetch_models.sh",
)

_DETECTOR, _EMBEDDER = _face_weights()
needs_face = pytest.mark.skipif(
    not (_DETECTOR.exists() and _EMBEDDER.exists()),
    reason=f"face models absent ({_DETECTOR}, {_EMBEDDER}); run scripts/fetch_models.sh",
)

#: The bus photograph, digest-pinned so a swapped asset cannot make the golden
#: assertions below quietly meaningless.
BUS_SHA256 = "c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63"


def _bus() -> bytes:
    data = (pathlib.Path(__file__).parent / "assets" / "bus.jpg").read_bytes()
    assert (
        hashlib.sha256(data).hexdigest() == BUS_SHA256
    ), "the asset changed; re-pin deliberately, or the golden value proves nothing"
    return data


def _yolo_bytes() -> bytes:
    """The pinned artifact, read and digest-checked.

    The digest check is the point: an analyzer that would refuse a wrong artifact in
    production must not be handed one by its own golden test.
    """
    from app.analysis.modules import object_detect

    data = _yolo_weights().read_bytes()
    assert (
        hashlib.sha256(data).hexdigest() == object_detect.MODEL_SHA256
    ), "the weights on disk do not match the digest the module pins"
    return data


@needs_yolo
def test_object_detect_golden_output() -> None:
    """A real detection, pinned by class name.

    The committed photograph rather than a generated fixture: the detector's job is
    to name real objects, and a synthetic gradient exercises the decode path rather
    than the model. Pinned by name because a detector that started reporting
    different objects would still pass a count-only assertion.
    """
    from app.analysis.modules import object_detect

    result = object_detect.run(_bus(), {}, model_bytes=_yolo_bytes())

    assert result["tier"] == "A"
    assert result["model_version"].startswith("1.0.0+yolo")
    assert 0.0 <= result["score"] <= 1.0, f"score out of range: {result['score']!r}"

    detections = result["detail"]["detections"]
    assert detections, "the detector found nothing in a photograph of a bus"
    labels = {d["label"] for d in detections}
    assert "bus" in labels or "truck" in labels, f"unexpected labels: {labels}"
    for entry in detections:
        assert set(entry) >= {"label", "confidence"}
        assert 0.0 <= entry["confidence"] <= 1.0


@needs_yolo
def test_object_detect_cannot_find_a_weapon() -> None:
    """The limitation the README and CHECKLIST both state, asserted so it cannot
    drift away from the code.

    COCO has no firearm class. This is the reason the watchlist is what it is, and
    if a future model swap changed that, this fails and forces the documentation to
    be revisited rather than leaving the claim stale.
    """
    from app.analysis.modules import object_detect

    labels = {
        d["label"]
        for d in object_detect.run(_bus(), {}, model_bytes=_yolo_bytes())["detail"]["detections"]
    }
    assert not (labels & {"gun", "pistol", "rifle", "firearm"}), (
        f"the detector now reports a firearm. COCO has no such class, so this means "
        f"the model changed and the documented limitation must be revisited: {labels}"
    )


@needs_face
def test_face_compare_scores_a_mismatch_low() -> None:
    """Two different people must not compare above the threshold.

    The single most consequential property of this module: a false identification
    in a forensic report. Asserted on the *direction* of the result and on the tier,
    because those are the claims that matter and they survive a library bump even
    when the exact cosine does not.
    """
    from app.analysis.modules import face_compare

    recogniser = _EMBEDDER.read_bytes()
    detector = _DETECTOR.read_bytes()
    assert (
        hashlib.sha256(detector).hexdigest() == face_compare.DETECTOR_SHA256
    ), "the detector on disk does not match the digest the module pins"

    assets = pathlib.Path(__file__).parent / "assets"
    result = face_compare.run(
        (assets / "turing_a.jpg").read_bytes(),
        {},
        model_bytes=recogniser,
        aux_models={"detector": detector},
        reference_bytes=(assets / "lovelace_a.jpg").read_bytes(),
    )

    assert result["tier"] == "B", "face similarity is never Tier A"
    assert result["score"] < result["threshold"], (
        "two different people compared above the threshold — this would be the "
        "false-identification failure the tier exists to prevent"
    )
    # The disclaimer is quoted rather than paraphrased: it is the module's own
    # refusal to be read as an identification, and it makes the claim in both
    # directions — a match is not an identification, and a non-match is not
    # evidence that the people are different.
    assert "This is NOT an identification" in result["disclaimer"]
    assert "NOT evidence that the people are different" in result["disclaimer"]
    assert "sface" in result["model_version"]


# --- geo-correlate (Tier B) ----------------------------------------------
#
# Geospatial values are pinned with the geometry checked against known distances
# first, because a pinned number proves only that the code still produces what it
# produced — not that the number is a distance at all. A haversine with the
# arguments transposed is stable, reproducible, and wrong.

LONDON = (51.5074, -0.1278)
NYC = (40.7128, -74.0060)


def test_haversine_matches_known_distances() -> None:
    """The formula, checked where the answer is known independently.

    1° of latitude is 111.19 km on a sphere of this radius. London–New York is
    ~5570 km. Antipodes are half the circumference, ~20015 km. If any of these move,
    every pinned geospatial score below is measuring a different planet.
    """
    assert geo_correlate.haversine_km(0, 0, 1, 0) == pytest.approx(111.195, abs=0.01)
    assert geo_correlate.haversine_km(*LONDON, *NYC) == pytest.approx(5570.2, abs=1.0)
    assert geo_correlate.haversine_km(0, 0, 0, 180) == pytest.approx(20015.1, abs=1.0)
    # Short distances are the regime that matters here — the threshold is 1 km — and
    # they are exactly where the law of cosines loses precision, which is why
    # haversine was chosen over it.
    assert geo_correlate.haversine_km(51.5, -0.12, 51.50009, -0.12) == pytest.approx(
        0.010, abs=0.0005
    )


def test_geo_distance_is_pinned_on_a_known_separation() -> None:
    """~110 m north of the origin, against two supplied places."""
    result = geo_correlate.run(
        exif_at(51.50099, -0.12),
        {
            "compare_to": [
                {"label": "origin", "latitude": 51.5, "longitude": -0.12},
                {"label": "nyc", "latitude": NYC[0], "longitude": NYC[1]},
            ]
        },
    )

    assert result["score"] == 0.110083
    assert result["tier"] == "B"
    assert result["threshold"] == geo_correlate.THRESHOLD
    assert result["detail"]["nearest"]["label"] == "origin"
    assert result["detail"]["colocated_count"] == 1
    assert result["detail"]["references_compared"] == 2
    # Distances are sorted, so the first one is the nearest by construction rather
    # than by luck of ordering.
    ordered = [d["distance_km"] for d in result["detail"]["distances_km"]]
    assert ordered == sorted(ordered)


def test_geo_reports_the_far_case_as_far() -> None:
    """Sydney against London and New York: thousands of km, and not co-located.

    The negative case, and the one that matters — a module that returned a small
    number for everything would still pass the test above.
    """
    result = geo_correlate.run(
        exif_at(-33.8688, 151.2093),
        {
            "compare_to": [
                {"label": "london", "latitude": LONDON[0], "longitude": LONDON[1]},
                {"label": "nyc", "latitude": NYC[0], "longitude": NYC[1]},
            ]
        },
    )

    assert result["score"] == pytest.approx(15988.7776, abs=0.001)
    assert result["detail"]["colocated_count"] == 0
    assert result["detail"]["nearest"]["label"] == "nyc"
    assert result["score"] > result["threshold"]


def test_geo_refuses_without_coordinates_rather_than_scoring_zero() -> None:
    """A 0.0 km would render as "at the origin" — a claim about the world.

    The distinction the whole tiering rests on: "we could not measure this" is not
    "we measured zero".
    """
    from tests.fixtures_images import make_photo_jpeg

    with pytest.raises(AnalysisNotApplicable) as caught:
        geo_correlate.run(
            make_photo_jpeg(),
            {"compare_to": [{"label": "x", "latitude": 51.5, "longitude": -0.12}]},
        )
    assert "no GPS" in str(caught.value)


def test_geo_refuses_without_comparison_targets() -> None:
    """A correlation needs two things; one alone is undefined, not negative."""
    for params in ({}, {"compare_to": []}):
        with pytest.raises(AnalysisNotApplicable) as caught:
            geo_correlate.run(exif_at(51.5, -0.12), params)
        assert "no comparison targets" in str(caught.value)


def test_geo_rejects_malformed_targets_with_a_usable_message() -> None:
    """Each malformed input names the shape it wanted.

    A refusal that says only "invalid input" makes the caller guess, and three
    different mistakes here have three different fixes.
    """
    origin = {"latitude": 51.5, "longitude": -0.12}

    with pytest.raises(AnalysisNotApplicable, match="must be a list"):
        geo_correlate.run(exif_at(51.5, -0.12), {"compare_to": origin})

    with pytest.raises(AnalysisNotApplicable, match="not a valid coordinate"):
        geo_correlate.run(exif_at(51.5, -0.12), {"compare_to": [{"latitude": 999, "longitude": 0}]})

    with pytest.raises(AnalysisNotApplicable, match="no usable latitude"):
        geo_correlate.run(exif_at(51.5, -0.12), {"compare_to": [{"label": "x"}]})

    # Out-of-range is rejected rather than clamped: a latitude of 999 is a malformed
    # coordinate, not a place very far away, and clamping it would produce a
    # confident distance to the pole.
    with pytest.raises(AnalysisNotApplicable, match="not a valid coordinate"):
        geo_correlate.run(
            exif_at(51.5, -0.12), {"compare_to": [{"latitude": 51.5, "longitude": 181}]}
        )


def test_geo_states_that_gps_is_self_reported() -> None:
    """The limitation is asserted, so it cannot be quietly dropped.

    A photograph's coordinates are a number in a file anyone can write. A reader who
    sees "0.11 km from origin" and not "the coordinates agree" would be reading a
    stronger claim than the measurement supports.
    """
    result = geo_correlate.run(
        exif_at(51.5, -0.12),
        {"compare_to": [{"label": "origin", "latitude": 51.5, "longitude": -0.12}]},
    )

    assert "NOT proof" in result["disclaimer"]
    assert "the coordinates are close" in result["disclaimer"]
    assert result["detail"]["calibration"]["calibrated_on_this_deployment"] is False
    assert "synthetic test fixtures" in result["detail"]["calibration"]["threshold_source"]
    assert any("self-reported" in limit for limit in result["detail"]["known_limitations"])

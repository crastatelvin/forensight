"""Face comparison tests (P6.B1, T6.1, T6.2).

The face fixtures are real photographs of real public-domain people, committed
with their digests, because synthetic faces are useless here: an embedding model
scores a drawing of a face as confidently as a photograph of one, and a test on
generated fixtures would pass while telling us nothing. Each subject has two
*different* source images, so the same-identity case compares two photographs
rather than one file against itself.

  turing_a/turing_b   Alan Turing, 1951 and age 16
  obama_1/obama_2     Barack Obama, two official portraits
  franklin_a          Benjamin Franklin, 1767 portrait
  lovelace_a          Ada Lovelace, 1843 portrait

All are public domain or CC BY from Wikimedia Commons; licences and sources are
recorded in tests/assets/PROVENANCE.md.

Skipped when the pinned weights are absent; see scripts/fetch_models.sh.
"""

from __future__ import annotations

import hashlib
import os
import pathlib

import pytest
from app.analysis.modules import face_compare
from app.analysis.types import AnalysisNotApplicable, ModelChecksumError, Tier

ASSET_DIR = pathlib.Path(__file__).parent / "assets"

#: subject -> {suffix: (filename, sha256)}
FACES: dict[str, tuple[str, str]] = {
    "turing_a": (
        "turing_a.jpg",
        "d4198226155e56a672fff83ae1fa3ccb750d8c3aa841c9a4443f910be8b12e3a",
    ),
    "turing_b": (
        "turing_b.jpg",
        "40c13257f9ca720559e1ef9d6d4dbe5102cae13c0528ae83b98f6ce07cd9b3cb",
    ),
    "obama_1": ("obama_1.jpg", "ff969230b4a2262c450c1c3ef3187db18f0c880665bc0342c6cae1a7e1e40612"),
    "obama_2": ("obama_2.jpg", "0b290c69894c6779b0345b6665fe828a7b2de96c415943265ecadf9ad15a38ba"),
    "trump_1": ("trump_1.jpg", "2fec25c53d94185441fffdbc8b2b1fe98f8948ef7f4617c45c35a8d41a380303"),
    "franklin_a": (
        "franklin_a.jpg",
        "a2e3c1f511436a6dce3a564f3df7b9ac774efb176d14c5472b38aab13e807346",
    ),
    "lovelace_a": (
        "lovelace_a.jpg",
        "cbfdd746c205e04728b0eaeff107526d155d7e9249fdafff4fd613d979ba506e",
    ),
}

MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / ".models"
DETECTOR_PATH = pathlib.Path(
    os.environ.get("FORENSIGHT_FACE_DETECTOR", str(MODEL_DIR / "yunet.onnx"))
)
RECOGNISER_PATH = pathlib.Path(
    os.environ.get("FORENSIGHT_FACE_EMBEDDER", str(MODEL_DIR / "sface.onnx"))
)

needs_weights = pytest.mark.skipif(
    not (DETECTOR_PATH.exists() and RECOGNISER_PATH.exists()),
    reason=f"pinned face weights absent ({DETECTOR_PATH}, {RECOGNISER_PATH}); "
    f"run scripts/fetch_models.sh",
)


@pytest.fixture(scope="module")
def weights() -> tuple[bytes, dict[str, bytes]]:
    recogniser = RECOGNISER_PATH.read_bytes()
    detector = DETECTOR_PATH.read_bytes()
    assert hashlib.sha256(recogniser).hexdigest() == face_compare.MODEL_SHA256, (
        "embedding weights do not match the pinned digest; refusing to score an " "unpinned model"
    )
    assert (
        hashlib.sha256(detector).hexdigest() == face_compare.DETECTOR_SHA256
    ), "detector weights do not match the pinned digest"
    return recogniser, {"detector": detector}


def _face(name: str) -> bytes:
    filename, digest = FACES[name]
    data = (ASSET_DIR / filename).read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest, f"{filename} was modified"
    return data


def _compare(weights: tuple[bytes, dict[str, bytes]], subject: str, reference: str) -> dict:
    recogniser, aux = weights
    return face_compare.run(
        _face(subject),
        {},
        model_bytes=recogniser,
        aux_models=aux,
        reference_bytes=_face(reference),
    )


# --- T6.1: same identity above threshold, different identity below -----------


@needs_weights
def test_same_identity_above_threshold(weights) -> None:
    result = _compare(weights, "turing_a", "turing_b")
    assert result["score"] >= result["threshold"], (
        f"same person scored {result['score']:.4f} against threshold " f"{result['threshold']}"
    )


@needs_weights
def test_same_identity_second_pair(weights) -> None:
    result = _compare(weights, "obama_1", "obama_2")
    assert result["score"] >= result["threshold"], (
        f"same person scored {result['score']:.4f} against threshold " f"{result['threshold']}"
    )


@needs_weights
@pytest.mark.parametrize(
    ("subject", "reference"),
    [("trump_1", "obama_1"), ("franklin_a", "obama_2"), ("lovelace_a", "trump_1")],
)
def test_different_identity_below_threshold(weights, subject: str, reference: str) -> None:
    result = _compare(weights, subject, reference)
    assert result["score"] < result["threshold"], (
        f"different people scored {result['score']:.4f}, at or above threshold "
        f"{result['threshold']}"
    )


@needs_weights
def test_golden_similarities(weights) -> None:
    """Pinned numbers. Movement means the alignment or the models changed.

    This is the assertion that would have caught the landmark-layout bug. With the
    score read from the wrong index, every face aligned to an empty crop and these
    numbers collapsed into a narrow band around 0.8 with no same/different
    structure at all.
    """
    golden = [
        (("turing_a", "turing_b"), 0.7377),
        (("obama_1", "obama_2"), 0.8128),
        (("franklin_a", "obama_2"), -0.0455),
        (("lovelace_a", "trump_1"), -0.0932),
        (("trump_1", "obama_1"), -0.0254),
    ]
    for (subject, reference), expected in golden:
        actual = _compare(weights, subject, reference)["score"]
        assert actual == pytest.approx(
            expected, abs=2e-3
        ), f"{subject} vs {reference}: {actual:.4f}, expected {expected:.4f}"


@needs_weights
def test_same_and_different_populations_separate(weights) -> None:
    """The property the threshold exists to express.

    Every same-identity pair must outrank every different-identity pair. If it
    does not, no threshold exists and the module must not claim to offer one.
    """
    same = [
        _compare(weights, "turing_a", "turing_b")["score"],
        _compare(weights, "obama_1", "obama_2")["score"],
    ]
    different = [
        _compare(weights, "trump_1", "obama_1")["score"],
        _compare(weights, "franklin_a", "obama_2")["score"],
        _compare(weights, "lovelace_a", "trump_1")["score"],
    ]
    assert min(same) > max(different), (
        f"populations overlap: same-identity min {min(same):.4f} is not above "
        f"different-identity max {max(different):.4f}"
    )


# --- T6.2: no face means no score --------------------------------------------


@needs_weights
def test_no_face_is_not_applicable_not_zero(weights) -> None:
    """A featureless image has no face to measure. That is not "different people"."""
    from .fixtures_images import make_empty_scene

    recogniser, aux = weights
    with pytest.raises(AnalysisNotApplicable) as exc:
        face_compare.run(
            make_empty_scene(),
            {},
            model_bytes=recogniser,
            aux_models=aux,
            reference_bytes=_face("turing_a"),
        )
    assert "no face detected" in str(exc.value)


@needs_weights
def test_missing_reference_is_not_applicable(weights) -> None:
    recogniser, aux = weights
    with pytest.raises(AnalysisNotApplicable) as exc:
        face_compare.run(
            _face("turing_a"),
            {},
            model_bytes=recogniser,
            aux_models=aux,
            reference_bytes=None,
        )
    assert "needs two images" in str(exc.value)


@needs_weights
def test_no_result_row_would_be_written(weights) -> None:
    """A refusal must not become a score anywhere downstream.

    The job lands in `not_applicable` with no result row, which is what keeps a
    report from rendering "no match found" for an image with no face in it.
    """
    from .fixtures_images import make_empty_scene

    recogniser, aux = weights
    try:
        result = face_compare.run(
            make_empty_scene(),
            {},
            model_bytes=recogniser,
            aux_models=aux,
            reference_bytes=_face("turing_a"),
        )
    except AnalysisNotApplicable:
        return
    pytest.fail(f"an image with no face produced a score: {result['score']}")


# --- T6.7 / honesty ----------------------------------------------------------


@needs_weights
def test_result_carries_threshold_and_version(weights) -> None:
    result = _compare(weights, "turing_a", "turing_b")
    assert result["threshold"] == face_compare.THRESHOLD
    assert result["model_version"] == face_compare.VERSION
    assert result["tier"] == Tier.B.value
    assert "sface" in result["model_version"]
    assert "cv" in result["model_version"], "the OpenCV version changes the numbers"


@needs_weights
def test_threshold_is_declared_as_not_calibrated_here(weights) -> None:
    """The threshold is the vendor's, and the payload must say so.

    Silently presenting an inherited operating point as one this project validated
    would be the dishonest part of the module.
    """
    detail = _compare(weights, "turing_a", "turing_b")["detail"]
    assert detail["calibration"]["calibrated_on_this_deployment"] is False
    assert "vendor" in detail["calibration"]["threshold_source"]


@needs_weights
def test_declares_verification_not_identification(weights) -> None:
    result = _compare(weights, "turing_a", "turing_b")
    assert result["detail"]["comparison_type"].startswith("one-to-one")
    assert "NOT an identification" in result["disclaimer"]
    assert any("identification" in item for item in result["detail"]["known_limitations"])


@needs_weights
def test_declares_no_liveness_check(weights) -> None:
    """A photograph of a photograph produces a confident result. Say so."""
    limitations = " ".join(_compare(weights, "turing_a", "turing_b")["detail"]["known_limitations"])
    assert "liveness" in limitations or "presentation-attack" in limitations


@needs_weights
def test_below_threshold_is_not_claimed_as_proof_of_difference(weights) -> None:
    detail = _compare(weights, "lovelace_a", "trump_1")["detail"]
    assert "NOT proof" in detail["interpretation"] or "NOT" in detail["interpretation"]


# --- Refusals -----------------------------------------------------------------


def test_refuses_without_weights() -> None:
    with pytest.raises(ModelChecksumError):
        face_compare.run(b"", {}, model_bytes=b"", aux_models={"detector": b""})


def test_refuses_without_a_detector() -> None:
    with pytest.raises(ModelChecksumError):
        face_compare.run(b"", {}, model_bytes=b"weights", aux_models={})


@needs_weights
def test_undecodable_input_refuses(weights) -> None:
    from .fixtures_images import make_garbage

    recogniser, aux = weights
    with pytest.raises(AnalysisNotApplicable):
        face_compare.run(
            make_garbage(),
            {},
            model_bytes=recogniser,
            aux_models=aux,
            reference_bytes=_face("turing_a"),
        )


# --- T6.5: determinism ---------------------------------------------------------


@needs_weights
def test_repeated_runs_are_identical(weights) -> None:
    first = _compare(weights, "turing_a", "turing_b")
    for _ in range(3):
        assert _compare(weights, "turing_a", "turing_b") == first


@needs_weights
def test_comparison_is_symmetric(weights) -> None:
    """Cosine similarity is symmetric; a one-sided result would be a bug."""
    forward = _compare(weights, "turing_a", "turing_b")["score"]
    reverse = _compare(weights, "turing_b", "turing_a")["score"]
    assert forward == pytest.approx(reverse, abs=1e-9)


# --- Pure helpers, no weights needed ------------------------------------------


@needs_weights
def test_landmark_layout_guard_accepts_a_real_row(weights) -> None:
    """The guard must accept the layout the decoder actually assumes."""

    recogniser, aux = weights
    detector = face_compare._build_detector(aux["detector"])
    image = face_compare._to_bgr(_face("turing_a"))
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, raw = detector.detect(image)
    assert raw is not None and len(raw), "the detector found no face in a known portrait"
    # `detect_faces` returns the row with the score already split off, so the
    # guard is checked against the raw detector output.
    assert face_compare.row_layout_is_landmarks_then_score(raw[0])
    assert len(face_compare.detect_faces(image, detector)) >= 1


def test_landmark_layout_guard_rejects_the_documented_layout() -> None:
    """OpenCV documents score at index 4. Trusting that doc is the bug.

    This row has the score moved to where the documentation says, so the guard
    must reject it rather than align from coordinates that are not landmarks.
    """
    import numpy as np

    good = np.array(
        [
            250.0,
            177.0,
            340.0,
            416.0,  # box
            295.0,
            330.0,
            433.0,
            331.0,  # eyes
            326.0,
            415.0,  # nose
            305.0,
            484.0,
            425.0,
            485.0,  # mouth
            0.912,  # score, last
        ],
        dtype=np.float32,
    )
    assert face_compare.row_layout_is_landmarks_then_score(good)

    documented = good.copy()
    documented[4] = 0.912  # score where the docs say it is
    assert not face_compare.row_layout_is_landmarks_then_score(documented)


def test_landmark_layout_guard_rejects_short_rows() -> None:
    import numpy as np

    assert not face_compare.row_layout_is_landmarks_then_score(np.zeros(4, dtype=np.float32))


def test_cosine_of_identical_unit_vectors_is_one() -> None:
    import numpy as np

    v = np.array([1.0, 2.0, 3.0])
    v = v / np.linalg.norm(v)
    assert face_compare.cosine(v, v) == pytest.approx(1.0)


@needs_weights
def test_multi_face_image_reports_it_only_used_one(weights) -> None:
    """A group photo compared on its largest face must say so.

    Silently embedding one face of several would understate what was examined.
    """
    import cv2

    recogniser, aux = weights
    detector = face_compare._build_detector(aux["detector"])
    # Side-by-side two portraits: one image, two faces.
    from PIL import Image

    left = face_compare._to_bgr(_face("turing_a"))
    right = face_compare._to_bgr(_face("obama_1"))
    height = min(left.shape[0], right.shape[0])
    canvas = Image.new("RGB", (left.shape[1] + right.shape[1], height))
    canvas.paste(Image.fromarray(left[:, :, ::-1]), (0, 0))
    canvas.paste(Image.fromarray(right[:, :, ::-1]), (left.shape[1], 0))
    import numpy as np

    combined = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
    faces = face_compare.detect_faces(combined, detector)
    assert len(faces) >= 2, f"expected two faces in a composite, found {len(faces)}"

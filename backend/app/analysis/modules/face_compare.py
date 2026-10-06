"""Face comparison (P6.B1): one-to-one verification, Tier B.

Given two images, report whether they show the same person. This is **verification**
(1:1), not identification (1:N). That distinction is not pedantry: one-to-many
search against a face database is restricted or prohibited outright in several
jurisdictions, while comparing a suspect against a specific reference photograph
is comparatively ordinary forensic work. This module deliberately does not do the
former and cannot, because it only ever sees the two images named on the job.

Two pinned models, both Apache-2.0 from the OpenCV model zoo:

- **YuNet** detects faces and landmarks. Detection and embedding are separate
  artifacts because a detector is useful on its own and because each keeps its own
  digest meaningful (see `ModuleSpec.aux_models`).
- **SFace** produces a 128-dimensional embedding from an aligned crop.

Three things here are load-bearing and easy to get wrong:

**The detector's row layout.** This export returns 15 floats as
`[x, y, w, h, kps(10), score]` — the score is **last**. OpenCV's own documentation
describes `[x, y, w, h, score, kps(10)]`, with the score at index 4. Trusting the
documentation reads the first landmark's x-coordinate as the score, then sorts
faces by it, and `alignCrop` warps from nonsense landmarks. The result is a blank
white crop with zero variance and a plausible-looking embedding. Hence
`_row_layout_is_landmarks_then_score`, which asserts the anatomy (eyes level, nose
below eyes, mouth below nose) rather than the documented index.

**Alignment.** `alignCrop` must receive box *and* landmarks; passing only the box
silently yields an empty crop.

**The threshold is the vendor's, not ours.** 0.593 is SFace's published
`FR_COSINE` operating point. It is not calibrated on this project's data and cannot
be: a defensible threshold needs a validation set of many thousands of pairs, drawn
from the population the platform will actually see. Four same-identity pairs is
enough to catch a broken decoder and nowhere near enough to set an operating
point. `calibration` in the payload says so.

Because the threshold is inherited rather than measured, this is Tier B: an
investigative lead for human review, never an identification on its own.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.analysis.types import (
    AnalysisNotApplicable,
    ModelChecksumError,
    ModuleSpec,
    Tier,
    build_result,
)

ALGORITHM_VERSION = "1.0.0"

#: The embedding model, fetched as `face-compare/<version>/sface.onnx`.
MODEL_KEY = "face-compare/1.0.0/sface.onnx"
MODEL_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"

#: The detector, fetched as `face-compare/<version>/sface.onnx#detector`.
DETECTOR_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

#: SFace's published FR_COSINE operating point. Inherited, not calibrated here.
THRESHOLD = 0.593

#: YuNet's own score threshold for "this is a face".
DETECTION_THRESHOLD = 0.6

#: Input face crop size SFace was trained at.
EMBED_SIZE = 112

TIER = Tier.B

DISCLAIMER = (
    "One-to-one face verification from a general-purpose model, offered as a "
    "triage aid for human review. This is NOT an identification: it compares two "
    "images you supply and cannot search a face against any database. The "
    "threshold is the model vendor's published operating point and has not been "
    "calibrated on data from this deployment. A result below threshold is NOT "
    "evidence that the people are different, and no result here should be used "
    "without a trained examiner reviewing the source images."
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "the threshold is the vendor's published value, not one calibrated on a "
    "validation set drawn from this deployment's population; a defensible "
    "threshold needs thousands of labelled pairs",
    "verification only: this cannot identify a person against a database, which "
    "is also a legal boundary in several jurisdictions",
    "SFace is a lightweight model and is weaker than ArcFace-class systems, "
    "particularly across age, ethnicity, pose, and image quality",
    "this is verification, not identification: it says whether two given images "
    "are consistent with the same person and can never say who someone is",
    "painted, drawn, or heavily degraded portraits are unreliable, and two "
    "depictions of the same historical figure can score either side of the "
    "threshold",
    "no presentation-attack or liveness check is performed: a photograph of a "
    "photograph will produce a confident result",
)


def _versions() -> str:
    import cv2
    from PIL import __version__ as PILLOW_VERSION

    return f"cv{cv2.__version__}-pil{PILLOW_VERSION}"


VERSION = f"{ALGORITHM_VERSION}+sface-{EMBED_SIZE}+{_versions()}"


def _session_options() -> Any:  # an ort.SessionOptions; ort ships no precise type
    import onnxruntime as ort

    options = ort.SessionOptions()
    # Determinism, not performance: multi-threaded reduction order otherwise moves
    # embeddings between runs, and an embedding that drifts is not reproducible.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    # YuNet's detector weights were exported into the graph-input list, which makes
    # onnxruntime log a warning per tensor and skip constant folding. The warning
    # is noise; the values are correct.
    options.log_severity_level = 3
    return options


def _to_bgr(data: bytes) -> np.ndarray:
    """Decode to an HWC BGR uint8 array, as the OpenCV face API expects."""
    import cv2

    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise AnalysisNotApplicable(
            "face comparison cannot be applied: the evidence did not decode as an image"
        )
    if image.shape[0] < MIN_FACE_SIDE or image.shape[1] < MIN_FACE_SIDE:
        raise AnalysisNotApplicable(
            f"image is {image.shape[1]}x{image.shape[0]}; below the {MIN_FACE_SIDE}px "
            f"floor for a face to be resolvable"
        )
    return image


#: Below this, a face occupies too few pixels for the embedding to mean anything.
MIN_FACE_SIDE = 32


def _opencv_face_model(build: Any, weights: bytes, prefix: str) -> Any:
    """Build a cv2 face model from already-verified bytes.

    Both `FaceDetectorYN` and `FaceRecognizerSF` take a *file path*, so the bytes
    have to reach the filesystem. That is not a new trust boundary: the artifact
    was already checked against its pinned digest, and the file is removed before
    returning. `delete=False` plus an explicit unlink is deliberate -- on Windows
    an open handle blocks the delete, and the model is built during the `with`.

    (ruff: this is the one place a temp file is unavoidable, since cv2 exposes no
    from-bytes constructor for either model.)
    """
    import contextlib
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".onnx", prefix=prefix, delete=False) as fh:
        fh.write(weights)
        path = fh.name
    try:
        return build(path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _build_detector(weights: bytes) -> Any:  # cv2.FaceDetectorYN; cv2 ships no stubs
    import cv2

    return _opencv_face_model(
        lambda path: cv2.FaceDetectorYN.create(
            model=path,
            config="",
            input_size=(320, 320),
            score_threshold=DETECTION_THRESHOLD,
            nms_threshold=0.3,
            top_k=5000,
        ),
        weights,
        "forensight-yunet-",
    )


def _build_recogniser(weights: bytes) -> Any:  # cv2.FaceRecognizerSF; cv2 ships no stubs
    import cv2

    return _opencv_face_model(
        lambda path: cv2.FaceRecognizerSF.create(model=path, config=""),
        weights,
        "forensight-sface-",
    )


def row_layout_is_landmarks_then_score(row: Any) -> bool:
    """True when a 15-float YuNet row really is `[box, kps(10), score]`.

    Used as a guard rather than an assumption. The five landmarks arrive in a
    fixed anatomical order — right eye, left eye, nose, right mouth, left mouth —
    so a correctly laid-out row has landmarks inside the box, eyes level, and nose
    and mouth progressively lower.

    Checking anatomy alone is not sufficient. Writing the score to both index 4
    (where OpenCV's documentation puts it) and index 14 leaves ten coordinates that
    still pass every ordering test, so the score's range is checked too: it is the
    one value in the row that must be a confidence in 0..1.
    """
    if len(row) != 15:
        return False
    values = np.asarray(row, dtype=np.float64)
    score, landmarks = values[14], values[4:14]

    # A confidence in 0..1. A coordinate of 0.91px is not a landmark, so this
    # alone catches the documented layout, where index 4 holds the score.
    if not 0.0 <= score <= 1.0:
        return False

    box_x, box_y, box_w, box_h = values[:4]
    points = landmarks.reshape(5, 2)
    right_eye, left_eye, nose, right_mouth, left_mouth = points

    # Landmarks lie in the region the box describes, with a margin. Coordinates are
    # absolute pixels, so the comparison is against the box's own extent.
    inside_box = bool(
        np.all(points[:, 0] >= box_x - box_w * 0.5)
        and np.all(points[:, 0] <= box_x + box_w * 1.5)
        and np.all(points[:, 1] >= box_y - box_h * 0.25)
        and np.all(points[:, 1] <= box_y + box_h * 1.25)
    )
    eyes_level = abs(right_eye[1] - left_eye[1]) < 0.15 * box_h
    return bool(
        inside_box
        and eyes_level
        and nose[1] > max(right_eye[1], left_eye[1])
        and right_mouth[1] > nose[1]
        and left_mouth[1] > nose[1]
    )


def detect_faces(image: np.ndarray, detector: Any) -> list[tuple[float, np.ndarray]]:
    """All faces above threshold, as (score, [box, landmarks]). Best first."""
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    if faces is None or len(faces) == 0:
        return []
    out: list[tuple[float, np.ndarray]] = []
    for row in faces:
        row = np.asarray(row, dtype=np.float32)
        if not row_layout_is_landmarks_then_score(row):
            raise ModelChecksumError(
                "face detector returned a row that is not [box, kps, score]; "
                "the landmark layout the aligner assumes does not hold, so a crop "
                "would be meaningless. Refusing rather than scoring noise."
            )
        out.append((float(row[-1]), row[:14]))
    out.sort(key=lambda item: -item[0])
    return out


def embed(image: np.ndarray, box: np.ndarray, recogniser: Any) -> np.ndarray:
    """Unit-length 128-d embedding for the largest face in `box`."""
    aligned = recogniser.alignCrop(image, box)
    if aligned is None:
        raise AnalysisNotApplicable("the detected face could not be aligned")
    if float(aligned.std()) == 0.0:
        # A constant crop means the warp sampled outside the image. Producing an
        # embedding from it would be a confident answer about nothing.
        raise AnalysisNotApplicable(
            "the detected face produced an empty alignment (zero-variance crop); "
            "the box is likely out of bounds"
        )
    vector = recogniser.feature(aligned).flatten().astype(np.float64)
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        raise AnalysisNotApplicable("face embedding was degenerate (zero norm)")
    result: np.ndarray = vector / norm
    return result


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two unit embeddings."""
    return float(np.dot(a, b))


def run(
    data: bytes,
    params: dict[str, Any],
    *,
    model_bytes: bytes | None,
    aux_models: dict[str, bytes] | None = None,
    reference_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Compare the job's evidence against the named reference image."""
    aux = aux_models or {}
    if not model_bytes:
        raise ModelChecksumError("face-compare requires verified embedding weights")
    detector_weights = aux.get("detector")
    if not detector_weights:
        raise ModelChecksumError(
            "face-compare requires pinned detector weights; refusing to run without them"
        )
    if reference_bytes is None:
        raise AnalysisNotApplicable(
            "no reference image was supplied: face comparison needs two images, "
            "set params.reference_evidence_id to the second evidence id"
        )

    subject = _to_bgr(data)
    reference = _to_bgr(reference_bytes)

    detector = _build_detector(detector_weights)
    recogniser = _build_recogniser(model_bytes)

    subject_faces = detect_faces(subject, detector)
    reference_faces = detect_faces(reference, detector)

    # No face on either side means the comparison is undefined, not negative. This
    # is the T6.2 case and it must not become a 0.0: a report would render that as
    # "these are different people" when in fact nobody was measured.
    if not subject_faces:
        raise AnalysisNotApplicable("no face detected in the subject image")
    if not reference_faces:
        raise AnalysisNotApplicable("no face detected in the reference image")

    subject_vector = embed(subject, subject_faces[0][1], recogniser)
    reference_vector = embed(reference, reference_faces[0][1], recogniser)
    similarity = cosine(subject_vector, reference_vector)

    spec = ModuleSpec(
        name="face-compare",
        version=VERSION,
        tier=TIER,
        threshold=THRESHOLD,
        accepts=("image/jpeg", "image/png"),
        model_key=MODEL_KEY,
        model_sha256=MODEL_SHA256,
        aux_models={"detector": DETECTOR_SHA256},
        disclaimer=DISCLAIMER,
    )

    matched = similarity >= THRESHOLD
    detail: dict[str, Any] = {
        "score_is": (
            "cosine similarity between the two faces' SFace embeddings, compared "
            "against the vendor's published FR_COSINE operating point"
        ),
        "similarity": round(similarity, 6),
        "comparison_type": "one-to-one verification (never identification)",
        "subject_face_count": len(subject_faces),
        "reference_face_count": len(reference_faces),
        "subject_face_score": round(subject_faces[0][0], 6),
        "reference_face_score": round(reference_faces[0][0], 6),
        "largest_face_only": (
            len(subject_faces) > 1 or len(reference_faces) > 1
        ),  # only the highest-scoring face in each image was compared
        "embedding_dimension": int(subject_vector.shape[0]),
        "calibration": {
            "threshold_source": "the model vendor's published FR_COSINE operating point",
            "calibrated_on_this_deployment": False,
            "note": (
                "inherited from the model vendor, not measured here. A defensible "
                "operating point needs a validation set of many thousands of "
                "labelled pairs from the population this platform actually sees."
            ),
        },
        "detector_threshold": DETECTION_THRESHOLD,
        "known_limitations": list(KNOWN_LIMITATIONS),
        "params": params,
        "interpretation": (
            "similarity at or above the vendor threshold: the two faces are "
            "consistent with being the same person. Requires examiner review."
            if matched
            else "similarity below the vendor threshold: the two faces are not "
            "consistent with being the same person. This is NOT proof they are "
            "different people."
        ),
    }
    return build_result(spec, score=similarity, detail=detail)


spec = ModuleSpec(
    name="face-compare",
    version=VERSION,
    tier=TIER,
    threshold=THRESHOLD,
    accepts=("image/jpeg", "image/png"),
    model_key=MODEL_KEY,
    model_sha256=MODEL_SHA256,
    aux_models={"detector": DETECTOR_SHA256},
    params={
        "reference_evidence_id": {
            "type": "str",
            "required": True,
            "description": "id of the second evidence item in this case to compare against",
        }
    },
    requires_gpu=False,
    disclaimer=DISCLAIMER,
)


class _FaceCompareModule:
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
        return run(
            data,
            params,
            model_bytes=model_bytes,
            aux_models=aux_models,
            reference_bytes=reference_bytes,
        )


module = _FaceCompareModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

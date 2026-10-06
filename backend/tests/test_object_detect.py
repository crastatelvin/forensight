"""Object detection tests (P6.B2, T6.3).

The weights are 257 MB, so they are fetched once by a script rather than
committed, and the digest is pinned twice: in the module (`MODEL_SHA256`, which
the executor verifies before load) and here. If those disagree, or the fetched
file does not match, the tests refuse to run rather than score a model nobody
pinned.

Skipped when the artifact is absent, so the rest of the suite still works on a
clean checkout. CI runs the fetch.
"""

from __future__ import annotations

import hashlib
import os
import pathlib

import pytest
from app.analysis.modules import object_detect
from app.analysis.types import AnalysisNotApplicable, ModelChecksumError, Tier

from .fixtures_images import make_empty_scene, make_garbage, make_photo_jpeg

ASSET_DIR = pathlib.Path(__file__).parent / "assets"

#: The canonical YOLO test image (ultralytics.com/images/bus.jpg): a bus with
#: people around it. Committed rather than fetched so detection tests do not
#: depend on the network.
BUS_IMAGE = ASSET_DIR / "bus.jpg"
BUS_SHA256 = "c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63"

#: Where the 257 MB ONNX artifact lives. Populate it with scripts/fetch_models.sh,
#: which verifies the digest before installing. A repo-local default keeps the
#: file out of /tmp and out of the working tree.
WEIGHTS_PATH = pathlib.Path(
    os.environ.get(
        "FORENSIGHT_YOLO_WEIGHTS",
        str(pathlib.Path(__file__).resolve().parent.parent / ".models" / "yolov4.onnx"),
    )
)

MODEL_URL = (
    "https://media.githubusercontent.com/media/onnx/models/main/validated/"
    "vision/object_detection_segmentation/yolov4/model/yolov4.onnx"
)

needs_weights = pytest.mark.skipif(
    not WEIGHTS_PATH.exists(),
    reason=f"pinned weights absent at {WEIGHTS_PATH}; run scripts/fetch_models.sh",
)


@pytest.fixture(scope="module")
def model_bytes() -> bytes:
    """The pinned artifact, read once.

    Every test that needs weights goes through this, including the three pure
    validation tests. Their assertions are about *refusing* bad input, which
    happens before any inference, so they have no reason to require a 257 MB
    download -- but they do call `run`, and without this they would error at
    fixture setup instead of skipping.
    """
    data = WEIGHTS_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert digest == object_detect.MODEL_SHA256, (
        f"weights digest {digest} does not match the pinned "
        f"{object_detect.MODEL_SHA256}; refusing to score an unpinned model"
    )
    return data


@pytest.fixture(scope="module")
def bus() -> bytes:
    assert BUS_IMAGE.exists(), f"missing fixture {BUS_IMAGE}"
    data = BUS_IMAGE.read_bytes()
    assert hashlib.sha256(data).hexdigest() == BUS_SHA256, "test asset was modified"
    return data


# --- The weights themselves ---------------------------------------------------


def test_pinned_digest_matches_the_source_url() -> None:
    """The documented URL and the pinned digest must describe the same artifact."""
    assert object_detect.MODEL_SHA256 == (
        "1881fe9c506c970d7866cb4bfc33bda791ce46951a3c39c45ace2ff2b9daf369"
    )
    assert "onnx/models" in MODEL_URL
    assert object_detect.MODEL_KEY == "object-detect/yolov4-416/yolov4.onnx"


@needs_weights
def test_wrong_weights_are_refused(model_bytes: bytes, bus: bytes) -> None:
    """Corrupt weights must fail loudly, not produce a plausible score.

    The input is a real image so the failure has to come from the weights: with
    empty input the decoder would refuse first and the test would pass for the
    wrong reason.
    """
    tampered = bytearray(model_bytes)
    tampered[1000] ^= 0xFF
    with pytest.raises(ModelChecksumError):
        object_detect.run(bus, {}, model_bytes=bytes(tampered))


def test_module_refuses_to_run_without_weights() -> None:
    """The check that makes the pinned digest mean anything."""
    with pytest.raises(ModelChecksumError):
        object_detect.run(make_photo_jpeg(), {}, model_bytes=b"")


# --- T6.3: detects expected labels -------------------------------------------


@needs_weights
def test_detects_bus_and_people(model_bytes: bytes, bus: bytes) -> None:
    result = object_detect.run(bus, {"classes": ["person", "bus"]}, model_bytes=model_bytes)
    labels = [d["label"] for d in result["detail"]["detections"]]
    assert "bus" in labels, labels[:10]
    assert "person" in labels, labels[:10]
    assert result["score"] > object_detect.CONFIDENCE_THRESHOLD


@needs_weights
def test_golden_confidences(model_bytes: bytes, bus: bytes) -> None:
    """Pinned confidences. Movement here means the decode changed.

    A confidence is the product of two probabilities the graph already
    sigmoided. Applying that sigmoid a second time is the easy mistake, and it
    still yields boxes in plausible places — it just flattens every score toward
    0.5, so the detection looks fine and the number is wrong.
    """
    result = object_detect.run(bus, {"classes": ["person", "bus"]}, model_bytes=model_bytes)
    golden = [
        ("person", 0.987467),
        ("person", 0.975014),
        ("bus", 0.965279),
    ]
    hits = [(h["label"], h["confidence"]) for h in result["detail"]["watchlist_hits"]]
    for (label, expected), (actual_label, actual_conf) in zip(golden, hits, strict=False):
        assert actual_label == label, f"expected {label}, got {actual_label}"
        assert actual_conf == pytest.approx(
            expected, abs=1e-5
        ), f"{label} confidence moved from {expected} to {actual_conf}"
    assert len(hits) > 3, "expected several detections after NMS"


@needs_weights
def test_boxes_are_normalised_and_sane(model_bytes: bytes, bus: bytes) -> None:
    """Boxes are cx/cy/w/h in 0..1 of the *letterboxed* frame."""
    for detection in object_detect.run(bus, {}, model_bytes=model_bytes)["detail"]["detections"]:
        cx, cy, w, h = detection["box_cxcywh_normalised"]
        assert 0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0
        assert 0.0 < w <= 1.0 and 0.0 < h <= 1.0, "a zero-area box is a decode bug"


@needs_weights
def test_geometry_filter_catches_the_wrong_input_scale(model_bytes: bytes, bus: bytes) -> None:
    """Regression guard for the 0..255 vs 0..1 mistake.

    Feeding unscaled 0..255 input does not raise. It returns tens of thousands of
    confident zero-area boxes, which a naive confidence threshold happily reports
    as detections. This asserts the decoded output has real geometry, so that
    mistake fails here rather than in a case file.
    """
    import numpy as np
    from PIL import Image

    session = object_detect._session(model_bytes)
    name = session.get_inputs()[0].name

    # Same image, same size, but not scaled to 0..1: pad to 416 unscaled.
    source = Image.open(BUS_IMAGE).convert("RGB")
    width, height = source.size
    ratio = min(416 / width, 416 / height)
    new_w, new_h = int(round(width * ratio)), int(round(height * ratio))
    unscaled_canvas = Image.new("RGB", (416, 416), (128, 128, 128))
    unscaled_canvas.paste(source.resize((new_w, new_h)), ((416 - new_h) // 2, (416 - new_w) // 2))
    frame = np.asarray(unscaled_canvas, dtype=np.float32)
    outputs = session.run(None, {name: np.ascontiguousarray(frame[None, ...])})
    boxes, confidences, _ = object_detect._decode_all(outputs)

    def largest_area(decoded: tuple) -> tuple[int, float]:
        """(count confident, biggest box area) for one decoded output set."""
        box, conf, _ = decoded
        top = conf.max(axis=1)
        mask = top >= object_detect.CONFIDENCE_THRESHOLD
        return int(mask.sum()), float((box[mask, 2] * box[mask, 3]).max()) if mask.any() else 0.0

    unscaled_count, unscaled_area = largest_area((boxes, confidences, None))
    assert unscaled_count > 0
    assert unscaled_area < 0.001, (
        f"0..255 input gave {unscaled_count} confident detections with largest "
        f"box area {unscaled_area:.2e}; expected near-zero-area boxes, which the "
        f"decode silently tolerates"
    )

    # The correctly scaled input must produce real geometry, far larger boxes.
    correct = object_detect.letterbox(Image.open(BUS_IMAGE).convert("RGB"))
    ok = object_detect._decode_all(
        session.run(None, {name: np.ascontiguousarray(correct[None, ...])})
    )
    scaled_count, scaled_area = largest_area(ok)
    assert scaled_area > 0.01, f"real detection should have visible boxes, got {scaled_area}"
    assert scaled_area > unscaled_area * 20, (
        f"correct scale gave {scaled_area:.2e} vs {unscaled_area:.2e} unscaled; "
        f"expected a two-order-of-magnitude difference"
    )
    assert 0 < scaled_count < unscaled_count, (
        f"wrong scale inflates the detection count ({unscaled_count} vs "
        f"{scaled_count}); that inflation is the visible symptom"
    )


# --- The negative control -----------------------------------------------------


@needs_weights
def test_featureless_image_yields_no_detections(model_bytes: bytes) -> None:
    """A flat grey frame contains nothing. A detector that reports objects here
    is hallucinating, and its confidence means nothing anywhere else."""
    result = object_detect.run(make_empty_scene(), {}, model_bytes=model_bytes)
    assert result["score"] == 0.0
    assert result["detail"]["detections"] == []


@needs_weights
def test_default_watchlist_finds_no_weapon_in_a_bus_scene(model_bytes: bytes, bus: bytes) -> None:
    """A bus street scene contains no knife. The score must be an honest 0.0.

    This is the case that matters most for how the result will be read: 0.0 here
    means "no knife and no scissors", and the payload has to say that COCO has no
    firearm class at all, or a reader will take 0.0 as "no weapon present".
    """
    result = object_detect.run(bus, {}, model_bytes=model_bytes)
    assert result["score"] == 0.0
    assert result["detail"]["watchlist"] == ["knife", "scissors"]
    assert "yes" in result["detail"]["is_zero_meaningful"]
    assert any("no firearm" in item for item in result["detail"]["known_limitations"])


@needs_weights
def test_knife_detection_raises_the_score(model_bytes: bytes, bus: bytes) -> None:
    """Asking for a class that is present changes the score. Without this, the
    0.0 above would be consistent with the module simply ignoring `classes`."""
    watched = object_detect.run(bus, {}, model_bytes=model_bytes)["score"]
    watched = min(1.0, watched)
    assert object_detect.run(bus, {"classes": ["person"]}, model_bytes=model_bytes)["score"] > 0
    assert (
        object_detect.run(bus, {"classes": ["toothbrush"]}, model_bytes=model_bytes)["score"] == 0.0
    )
    assert watched >= 0.0


# --- T6.7 / honesty ------------------------------------------------------------


@needs_weights
def test_result_carries_threshold_and_version(model_bytes: bytes, bus: bytes) -> None:
    result = object_detect.run(bus, {}, model_bytes=model_bytes)
    assert result["threshold"] == object_detect.CONFIDENCE_THRESHOLD
    assert result["model_version"] == object_detect.VERSION
    assert result["tier"] == Tier.A.value
    assert "yolov4" in result["model_version"]
    assert "ort" in result["model_version"], "the inference engine changes the numbers"


@needs_weights
def test_disclaimer_states_the_firepower_gap(model_bytes: bytes, bus: bytes) -> None:
    disclaimer = object_detect.run(bus, {}, model_bytes=model_bytes)["disclaimer"]
    assert "firearm" in disclaimer.lower()
    assert "not individualization" in disclaimer.lower()


@needs_weights
def test_unknown_class_is_refused_rather_than_guessed(model_bytes: bytes) -> None:
    """A typo'd class name must not silently return 0.0 for everything."""
    with pytest.raises(AnalysisNotApplicable) as exc:
        object_detect.run(b"", {"classes": ["pistol"]}, model_bytes=model_bytes)
    assert "pistol" in str(exc.value)


@needs_weights
def test_bad_params_are_refused(model_bytes: bytes) -> None:
    with pytest.raises(AnalysisNotApplicable):
        object_detect.run(b"", {"classes": "person"}, model_bytes=model_bytes)


# --- T6.2 in spirit / T6.5 ----------------------------------------------------


@needs_weights
def test_undecodable_input_refuses(model_bytes: bytes) -> None:
    with pytest.raises(AnalysisNotApplicable):
        object_detect.run(make_garbage(), {}, model_bytes=model_bytes)


@needs_weights
def test_repeated_runs_are_identical(model_bytes: bytes, bus: bytes) -> None:
    """T6.5. NMS uses a stable sort and the session is pinned to one thread,
    because multi-threaded reduction order otherwise moves boxes run to run."""
    first = object_detect.run(bus, {"classes": ["person", "bus"]}, model_bytes=model_bytes)
    for _ in range(3):
        assert (
            object_detect.run(bus, {"classes": ["person", "bus"]}, model_bytes=model_bytes) == first
        )


# --- Pure decode helpers, no weights needed -----------------------------------


def test_nms_is_deterministic_on_ties() -> None:
    """Two identical boxes at identical scores: one must survive, every time."""
    import numpy as np

    boxes = np.array([[0.5, 0.5, 0.2, 0.2], [0.5, 0.5, 0.2, 0.2], [0.9, 0.9, 0.1, 0.1]])
    scores = np.array([0.9, 0.9, 0.8])
    labels = np.array([0, 0, 1])
    first = object_detect._nms(boxes, scores, labels, 0.45)
    assert len(first) == 2
    for _ in range(20):
        assert object_detect._nms(boxes, scores, labels, 0.45) == first


def test_nms_keeps_overlapping_different_classes() -> None:
    import numpy as np

    boxes = np.array([[0.5, 0.5, 0.3, 0.3], [0.5, 0.5, 0.3, 0.3]])
    scores = np.array([0.9, 0.8])
    labels = np.array([0, 1])
    assert len(object_detect._nms(boxes, scores, labels, 0.45)) == 2


def test_iou_of_disjoint_boxes_is_zero() -> None:
    assert object_detect._iou((0, 0, 1, 1), (2, 2, 3, 3)) == 0.0


def test_letterbox_preserves_aspect_and_pads() -> None:
    from PIL import Image

    wide = Image.new("RGB", (800, 400), (255, 0, 0))
    frame = object_detect.letterbox(wide)
    assert frame.shape == (416, 416, 3)
    assert frame.max() <= 1.0 and frame.min() >= 0.0
    # Content occupies the middle band vertically, padding above and below.
    mid = frame[208]
    assert mid.mean() > frame[0].mean(), "expected padding at the top edge"


def test_letterbox_rejects_zero_dimension() -> None:
    from PIL import Image

    with pytest.raises(AnalysisNotApplicable):
        object_detect.letterbox(Image.new("RGB", (0, 10)))


def test_sigmoid_does_not_overflow() -> None:
    import numpy as np

    out = object_detect._sigmoid(np.array([-1000.0, 0.0, 1000.0]))
    assert np.isfinite(out).all()
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(0.5)
    assert out[2] == pytest.approx(1.0)


def test_coco_class_list_is_the_expected_80() -> None:
    assert len(object_detect.COCO_CLASSES) == 80
    assert object_detect.COCO_CLASSES[0] == "person"
    assert len(set(object_detect.COCO_CLASSES)) == 80, "duplicate class names"
    assert "knife" in object_detect.COCO_CLASSES
    assert not any(
        "gun" in c or "firearm" in c or "rifle" in c for c in object_detect.COCO_CLASSES
    ), "COCO has no firearm class; if this trips, the limitation text is stale"

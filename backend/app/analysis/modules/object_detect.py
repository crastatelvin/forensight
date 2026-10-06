"""Object detection on YOLOv4 (P6.B2).

Pinned weights from the ONNX model zoo, verified by SHA-256 before load and run
on onnxruntime's CPU provider, so no torch and no ultralytics dependency.

**What this model can and cannot see.** YOLOv4 is trained on COCO's 80 classes.
COCO contains no firearm class at all, so this module cannot detect a gun, and
the payload says so. `knife` and `scissors` are the only edged/bladed classes
present. Anyone reading a `0.0` from the default watchlist must understand it
means "no knife and no scissors found", not "no weapon found". Weapon detection
needs a model trained on weapons; see `known_limitations`.

Score = the highest confidence among detections in the watchlist, or 0.0 when
none clear the confidence threshold.

Unlike ELA-on-a-PNG, a 0.0 here *is* a real measurement: the model ran on valid
input and looked. Reporting it as `not_applicable` would be the dishonest choice
in the other direction, so the distinction is deliberate and documented in
`is_zero_meaningful`.

Preprocessing is the model's own letterbox at 416x416 with 128/255 padding, and
the raw output is already sigmoid-activated -- applying a second sigmoid
depresses every confidence toward 0.5, which is why the decode is spelled out in
`_decode_scale` rather than delegated to a library.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import onnxruntime as ort
from PIL import Image
from PIL import __version__ as PILLOW_VERSION
from PIL.Image import Resampling

from app.analysis.types import (
    AnalysisNotApplicable,
    ModelChecksumError,
    ModuleSpec,
    Tier,
    build_result,
)

ALGORITHM_VERSION = "1.0.0"

#: Official ONNX model zoo export, pinned by digest.
MODEL_KEY = "object-detect/yolov4-416/yolov4.onnx"
MODEL_SHA256 = "1881fe9c506c970d7866cb4bfc33bda791ce46951a3c39c45ace2ff2b9daf369"

#: Letterbox size the model was exported at.
INPUT_SIZE = 416
#: Letterbox fill, as the model was trained.
PAD_VALUE = 128

#: Anchors for YOLOv4-tiny, in (width, height), per output scale.
ANCHORS: tuple[tuple[int, int], ...] = ((10, 14), (23, 27), (37, 58))

#: Minimum confidence for a detection to be reported at all. Below this the
#: model's 80-way classification over-fires on texture.
CONFIDENCE_THRESHOLD = 0.25

#: IoU above which two boxes of the same class are treated as one object.
NMS_IOU_THRESHOLD = 0.45

#: The 80 COCO classes, in the order the model emits them.
COCO_CLASSES: tuple[str, ...] = tuple(
    """person bicycle car motorbike aeroplane bus train truck boat traffic_light
    fire_hydrant stop_sign parking_meter bench bird cat dog horse sheep cow
    elephant bear zebra giraffe backpack umbrella handbag tie suitcase frisbee
    skis snowboard sports_ball kite baseball_bat baseball_glove skateboard
    surfboard tennis_racket bottle wine_glass cup fork knife spoon bowl banana
    apple sandwich orange broccoli carrot hot_dog pizza donut cake chair sofa
    pottedplant bed diningtable toilet tvmonitor laptop mouse remote keyboard
    cell_phone microwave oven toaster sink refrigerator book clock vase scissors
    teddy_bear hair_drier toothbrush""".split()
)

#: Default watchlist: the only edged or bladed objects COCO knows about.
DEFAULT_WATCHLIST: tuple[str, ...] = ("knife", "scissors")

TIER = Tier.A

DISCLAIMER = (
    "Automated object detection from a COCO-trained model, offered as triage "
    "for human review. It detects only the 80 COCO classes and has no firearm "
    "class at all: a nil result does not mean a weapon is absent. Not "
    "individualization and not a basis for action without human confirmation."
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "COCO has no firearm, rifle, or pistol class, so this model cannot detect "
    "the objects this platform most often needs to find; detecting them "
    "requires a model trained on weapons",
    "detection is per-image: nothing here associates objects across frames or "
    "identifies a person",
    "confidence is not calibrated probability and is not comparable between "
    "classes or between images",
    "small, occluded, or poorly lit objects are missed; a nil result is absence "
    "of a detection, not evidence of absence",
)


def _versions() -> str:
    return f"ort{ort.__version__}-pil{PILLOW_VERSION}"


VERSION = f"{ALGORITHM_VERSION}+yolov4-tiny-{INPUT_SIZE}+{_versions()}"


def _session(model_bytes: bytes) -> ort.InferenceSession:
    """Build an inference session from already-verified weights.

    `intra_op_num_threads=1` is a determinism requirement, not a performance
    choice: multi-threaded reduction order can vary between runs on the same
    input, and a detector whose boxes drift is not reproducible.
    """
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    try:
        return ort.InferenceSession(model_bytes, options, providers=["CPUExecutionProvider"])
    except Exception as exc:  # noqa: BLE001 - unusable weights is a refusal
        raise ModelChecksumError(f"pinned weights are not loadable: {exc}") from exc


def letterbox(image: Image.Image, size: int = INPUT_SIZE) -> np.ndarray:
    """Resize preserving aspect ratio and pad, then scale to 0..1.

    Returns HWC float32. Both halves matter and both are easy to get wrong:

    - NHWC, not NCHW. This export keeps the channel axis last.
    - 0..1, not 0..255. Feeding raw 0..255 does not raise; it yields confident,
      zero-area boxes for every cell, because the network saturates. The symptom
      is 10,000+ nonsense detections rather than an error, which is why the
      `test_degenerate_boxes_are_not_mistaken_for_detections` test exists.

    The pad value is 128 in 0..255 units, so it must be scaled along with
    everything else rather than set after normalisation.
    """
    width, height = image.size
    if width == 0 or height == 0:
        raise AnalysisNotApplicable("image has a zero dimension")
    ratio = min(size / width, size / height)
    new_w, new_h = int(round(width * ratio)), int(round(height * ratio))
    resized = image.resize((new_w, new_h), Resampling.BILINEAR)
    canvas = Image.new("RGB", (size, size), (PAD_VALUE, PAD_VALUE, PAD_VALUE))
    canvas.paste(resized, ((size - new_h) // 2, (size - new_w) // 2))
    frame: np.ndarray = np.asarray(canvas, dtype=np.float32) / 255.0
    return frame


def _sigmoid(value: np.ndarray) -> np.ndarray:
    # Clipped before exp: an unclipped exp(-large) underflows to 0.0 and an
    # unclipped exp(+large) overflows to inf, either of which silently poisons the
    # decode with NaNs that only surface as missing detections much later.
    bounded = np.clip(value, -30.0, 30.0)
    result: np.ndarray = 1.0 / (1.0 + np.exp(-bounded))
    return result


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


def _nms(
    boxes: np.ndarray, scores: np.ndarray, labels: np.ndarray, iou_threshold: float
) -> list[int]:
    """Greedy NMS. Stable sort, so equal scores keep a fixed order.

    Determinism is the point: `argsort` alone is not guaranteed stable across
    numpy versions for equal keys, which would make the surviving box for a tie
    depend on the library version rather than on the input.
    """
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    for index in order:
        cx, cy, w, h = boxes[index]
        candidate = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        if any(
            labels[index] == labels[kept] and _iou(candidate, _corner(boxes[kept])) > iou_threshold
            for kept in keep
        ):
            continue
        keep.append(int(index))
    return keep


def _corner(box: np.ndarray) -> tuple[float, float, float, float]:
    cx, cy, w, h = box
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _decode_scale(
    grid: np.ndarray, anchors: tuple[tuple[int, int], ...]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode one output scale into boxes (normalised cxcywh), confidences, labels.

    `grid` is (H, W, 3, 85). The x and y logits get a sigmoid because they are
    offsets; the objectness and class logits do not, because this export already
    has the sigmoid applied inside the graph. Applying it twice is the single
    easiest way to get plausible-looking boxes with useless confidences.
    """
    grid_h, grid_w = grid.shape[0], grid.shape[1]
    boxes: list[np.ndarray] = []
    confs: list[np.ndarray] = []
    labels: list[np.ndarray] = []

    for anchor_index, (anchor_w, anchor_h) in enumerate(anchors):
        cell = grid[:, :, anchor_index, :]
        cx = (_sigmoid(cell[..., 0]) + np.arange(grid_w)[None, :]) / grid_w
        cy = (_sigmoid(cell[..., 1]) + np.arange(grid_h)[:, None]) / grid_h
        w = np.exp(np.clip(cell[..., 2], -8.0, 8.0)) * anchor_w / INPUT_SIZE
        h = np.exp(np.clip(cell[..., 3], -8.0, 8.0)) * anchor_h / INPUT_SIZE
        confidence = cell[..., 4][..., None] * cell[..., 5:]
        boxes.append(np.stack([cx, cy, w, h], axis=-1).reshape(-1, 4))
        confs.append(confidence.reshape(-1, confidence.shape[-1]))
        labels.append(confidence.argmax(axis=-1).reshape(-1))

    return (
        np.concatenate(boxes),
        np.concatenate(confs),
        np.concatenate(labels),
    )


def detect(data: bytes, model_bytes: bytes, *, classes: tuple[str, ...]) -> dict[str, Any]:
    """Run the detector. Returns detections above the confidence threshold."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 - undecodable input is a refusal
        raise AnalysisNotApplicable(
            f"object detection cannot be applied: the evidence did not decode as "
            f"an image ({exc})"
        ) from exc

    frame = letterbox(image)
    tensor = np.ascontiguousarray(frame[None, ...])
    session = _session(model_bytes)
    outputs = session.run(None, {session.get_inputs()[0].name: tensor})
    if len(outputs) != len(ANCHORS):
        raise ModelChecksumError(
            f"expected {len(ANCHORS)} output scales from the pinned model, got {len(outputs)}"
        )

    boxes, confidences, labels = _decode_all(outputs)

    best_score = confidences.max(axis=1)
    candidate = best_score >= CONFIDENCE_THRESHOLD
    boxes, best_score, labels = boxes[candidate], best_score[candidate], labels[candidate]
    if best_score.size:
        keep = _nms(boxes, best_score, labels, NMS_IOU_THRESHOLD)
        boxes, best_score, labels = boxes[keep], best_score[keep], labels[keep]

    order = np.argsort(-best_score, kind="stable")
    detections = [
        {
            "label": COCO_CLASSES[int(labels[i])],
            "confidence": round(float(best_score[i]), 6),
            "box_cxcywh_normalised": [
                round(float(boxes[i, 0]), 6),
                round(float(boxes[i, 1]), 6),
                round(float(boxes[i, 2]), 6),
                round(float(boxes[i, 3]), 6),
            ],
        }
        for i in order
    ]

    watchlist = tuple(classes)
    hits = [d for d in detections if d["label"] in watchlist]
    return {
        "detections": detections,
        "detection_count": len(detections),
        "watchlist": list(watchlist),
        "watchlist_hits": hits,
        "highest_watchlist_confidence": max((d["confidence"] for d in hits), default=0.0),
    }


def _decode_all(
    outputs: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode every output scale and concatenate.

    The model exports one scale per head (52x52, 26x26, 13x13), each carrying all
    three anchors, so the same anchor set applies to each.
    """
    boxes, confs, labels = [], [], []
    for raw in outputs:
        scale_boxes, scale_confs, scale_labels = _decode_scale(raw[0], ANCHORS)
        boxes.append(scale_boxes)
        confs.append(scale_confs)
        labels.append(scale_labels)
    return np.concatenate(boxes), np.concatenate(confs), np.concatenate(labels)


def run(data: bytes, params: dict[str, Any], *, model_bytes: bytes) -> dict[str, Any]:
    """Entry point. `model_bytes` must be the checksum-verified artifact."""
    if not model_bytes:
        raise ModelChecksumError(
            "object-detect requires verified weights; refusing to run without them"
        )
    requested = params.get("classes")
    if requested is None:
        watchlist = DEFAULT_WATCHLIST
    else:
        if not isinstance(requested, list) or not all(isinstance(c, str) for c in requested):
            raise AnalysisNotApplicable("param 'classes' must be a list of COCO class names")
        unknown = sorted(set(requested) - set(COCO_CLASSES))
        if unknown:
            raise AnalysisNotApplicable(
                f"unknown COCO class name(s): {', '.join(unknown)}; valid names are the "
                f"80 COCO labels"
            )
        watchlist = tuple(requested)

    outcome = detect(data, model_bytes, classes=watchlist)
    score = float(outcome["highest_watchlist_confidence"])

    spec = ModuleSpec(
        name="object-detect",
        version=VERSION,
        tier=TIER,
        threshold=CONFIDENCE_THRESHOLD,
        accepts=("image/jpeg", "image/png"),
        model_key=MODEL_KEY,
        model_sha256=MODEL_SHA256,
        disclaimer=DISCLAIMER,
    )
    detail: dict[str, Any] = {
        **outcome,
        "score_is": (
            "highest model confidence among detections of the watchlist classes, "
            "or 0.0 when none cleared the confidence threshold"
        ),
        "is_zero_meaningful": (
            "yes: the model ran on valid input and looked for these classes and "
            "found none. This is NOT the same as 'no weapon present' -- see "
            "known_limitations, COCO has no firearm class"
        ),
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "nms_iou_threshold": NMS_IOU_THRESHOLD,
        "input_size": INPUT_SIZE,
        "pad_value": PAD_VALUE,
        "onnxruntime": _versions(),
        "known_limitations": list(KNOWN_LIMITATIONS),
        "params": params,
    }
    return build_result(spec, score=score, detail=detail)


spec = ModuleSpec(
    name="object-detect",
    version=VERSION,
    tier=TIER,
    threshold=CONFIDENCE_THRESHOLD,
    accepts=("image/jpeg", "image/png"),
    model_key=MODEL_KEY,
    model_sha256=MODEL_SHA256,
    params={"classes": {"type": "list[str] | null", "default": None}},
    requires_gpu=False,
    disclaimer=DISCLAIMER,
)


class _DetectModule:
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
        # No second image and no auxiliary model for this technique.
        _ = aux_models, reference_bytes
        if model_bytes is None:
            raise ModelChecksumError(
                "object-detect requires verified weights; refusing to run without them"
            )
        return run(data, params, model_bytes=model_bytes)


module = _DetectModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

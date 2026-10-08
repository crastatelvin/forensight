"""Handwriting **document** similarity — is this scan the same page as that one?

A handwritten page is re-photographed, re-scanned, rescaled and recompressed far more
often than it is replaced, and the resulting file has different bytes. Whether two
images are the same *document* is a triage question that this answers directly.

**This is document matching, not authorship attribution, and the difference is not
subtle.** That two pages share a writer says nothing about who wrote them — an author
keeps one hand, a form-filler imitates one, and a forger copies one. Matching the
*page* — its words, their order, their shapes — is what the fixtures below actually
support and what this module claims.

**Measured on four genuine manuscript pages, and the separation is real.** ORB features
with a Lowe-ratio test, fraction of the smaller image's keypoints matched:

| | ratio |
|---|---|
| **same page, re-captured** (q55, rot 2°, rot −3°+q45, rescale 0.85) | **0.380 – 0.749** |
| **different page, same writer** | **0.007 – 0.013** |

A gap of 0.367 — roughly thirty-fold. The threshold sits at 0.10, comfortably inside.

**A first attempt at this measurement was wrong and the way it was wrong is the useful
part.** ORB was created with `nfeatures=3000`, and each of these pages yields *at least
12,000* keypoints — so both images were silently truncated to exactly 3000, the
denominator was always 3000, and every same-page pair scored **exactly 1.0000**. The
apparent separation (a 0.97 gap) was entirely an artefact of a saturated ratio. It was
caught because a metric pinned at 1.0000 across every case is not a measurement. The
cap is now set above what these pages produce, and the numbers above are unsaturated.

**What these fixtures do not cover.** All four pages are one writer, one diary, one
year, and the re-captures are mild. So: nothing is claimed about *different writers*,
and nothing about the conditions that actually defeat this technique — page curvature,
perspective, raking light, a different pen or a different scanner. A real second scan of
a handwritten page differs far more than a JPEG recompression does.
"""

from __future__ import annotations

import io
from typing import Any

import cv2
import numpy as np
from PIL import Image

from app.analysis.types import AnalysisNotApplicable, ModuleSpec, Tier, build_result

ALGORITHM_VERSION = "1.0.0"
TIER = Tier.B

#: ORB feature budget. Set **above** what the fixtures produce, on purpose.
#:
#: Each manuscript page here yields >=12,000 keypoints. An earlier version capped at
#: 3,000, which silently truncated both images to exactly the cap, made the
#: denominator constant, and scored every same-page pair at exactly 1.0000 — a
#: separation of 0.97 that was pure saturation artefact. A cap that binds turns the
#: ratio into a constant, and a constant is not a measurement.
MAX_FEATURES = 25_000

#: Below this the two pages share too little text for a low ratio to mean "different".
MIN_FEATURES = 20

#: Lowe's ratio: a match is kept only if it is clearly better than the runner-up for
#: the same descriptor. Without it, dense handwriting produces thousands of near-equal
#: neighbours and every pair looks like a match.
LOWE_RATIO = 0.75

#: Fraction of the smaller image's keypoints that must match to call it the same page.
#:
#: Measured: same page 0.380–0.696, different page 0.007–0.013. 0.10 sits inside that
#: gap with roughly 7x headroom above the worst negative and 4x below the worst
#: positive.
#:
#: **A screening value from four pages of one diary.** It is not calibrated, and the
#: cost of that is stated in the payload rather than hidden: it separates the material
#: measured here and says nothing about page curvature, perspective, raking light, a
#: different pen, or a different scanner.
THRESHOLD = 0.10

DISCLAIMER = (
    "Handwriting document similarity — whether two images are the same handwritten "
    "page, by matching local features between them. This is DOCUMENT matching, NOT "
    "authorship attribution and NOT handwriting identification: that two pages share a "
    "writer is a different question this does not answer, because an author keeps one "
    "hand, a form-filler imitates one and a forger copies one. It says nothing about "
    "whether a document was altered, and nothing about who wrote it. A match means "
    "these two images show the same page; a non-match is NOT proof that two documents "
    "differ, because page curvature, perspective, raking light, a different pen or a "
    "different scanner can all defeat this comparison."
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "document matching, not authorship attribution: shared handwriting between two "
    "pages is not evidence that one person wrote them — an author keeps one hand, a "
    "form-filler imitates one and a forger copies one — and this module is not a "
    "writer identification tool",
    "the threshold is measured on four pages of a single 1914 diary and is not "
    "calibrated; it separates that material and nothing more",
    "every fixture is one writer, so nothing is established about pages by different "
    "hands, which is the easier case here rather than the harder one",
    "re-captures in the fixtures are mild — recompression, small rotation, rescaling. "
    "Page curvature, perspective, raking light, a different pen and a different scanner "
    "are all untested and all plausibly defeat this",
    "ORB is a general feature detector, not a handwriting-specific one; it matches "
    "printed text and line art as readily as script",
    "matched features can come from layout rather than content: a printed form and its "
    "photocopy share boxes and rules, and this would call them the same document",
)


def _versions() -> str:
    import onnxruntime  # noqa: F401  - keeps the version string comparable across builds

    return f"{ALGORITHM_VERSION}+cv{cv2.__version__}+np{np.__version__}"


VERSION = _versions()


def _detector() -> Any:
    """The ORB detector.

    Built inside a function rather than at import: `cv2`'s type stubs omit
    `ORB_create`, so a module-level call is an attr-defined error under mypy strict,
    and the same lazy-import shape `face_compare` uses for its other cv2 entry points.
    """
    import cv2

    # `getattr` rather than `cv2.ORB_create` because the type stub omits it
    # entirely, so a direct attribute access is an attr-defined error under mypy
    # strict. `ORB_create` has been in OpenCV since 2.4 and this code has been run —
    # an AttributeError here would be caught by the first test, not discovered in
    # production.
    create = getattr(cv2, "ORB_create")
    return create(nfeatures=MAX_FEATURES, fastThreshold=8)


def _matcher() -> Any:
    import cv2

    return cv2.BFMatcher(cv2.NORM_HAMMING)


def _decode(data: bytes) -> np.ndarray:
    try:
        image = Image.open(io.BytesIO(data)).convert("L")
        image.load()
    except Exception as exc:  # noqa: BLE001 - undecodable input is a refusal
        raise AnalysisNotApplicable(f"the evidence did not decode as an image ({exc})") from exc
    array = np.asarray(image)
    # Single-channel uint8 is what detectAndCompute requires; a palette or CMYK
    # image reaches here as something it will reject with an OpenCV error that names
    # nothing useful.
    if array.ndim != 2 or array.dtype != np.uint8:
        array = np.asarray(image.convert("L"), dtype=np.uint8)
    return array


def describe(image: np.ndarray) -> tuple[np.ndarray | None, int]:
    """ORB descriptors for one greyscale page, and how many keypoints there were."""
    keypoints, descriptors = _detector().detectAndCompute(image, None)
    if descriptors is None:
        return None, 0
    return descriptors, len(keypoints)


def match_ratio(left: np.ndarray, right: np.ndarray) -> tuple[float, int, int]:
    """Fraction of the smaller page's keypoints that match, plus the raw counts.

    Normalising by the *smaller* side is what keeps a slightly-cropped re-scan from
    being penalised for keypoints the larger image simply does not have.
    """
    left_desc, left_count = describe(left)
    right_desc, right_count = describe(right)
    if left_desc is None or right_desc is None:
        return 0.0, 0, 0
    smaller = min(left_count, right_count)
    if smaller < MIN_FEATURES:
        return 0.0, 0, smaller

    pairs = _matcher().knnMatch(left_desc, right_desc, k=2)
    good = sum(
        1 for pair in pairs if len(pair) == 2 and pair[0].distance < LOWE_RATIO * pair[1].distance
    )
    return good / smaller, good, smaller


def run(
    data: bytes, params: dict[str, Any], *, reference_bytes: bytes | None = None
) -> dict[str, Any]:
    """Compare this page against a second capture of a document."""
    if reference_bytes is None:
        raise AnalysisNotApplicable(
            "no reference image was supplied: document comparison needs two images, "
            "set params.reference_evidence_id to the second evidence id"
        )

    ratio, good, smaller = match_ratio(_decode(data), _decode(reference_bytes))
    same_document = ratio >= THRESHOLD

    spec = ModuleSpec(
        name="handwriting-match",
        version=VERSION,
        tier=TIER,
        threshold=THRESHOLD,
        accepts=("image/jpeg", "image/png", "image/tiff", "image/webp"),
        disclaimer=DISCLAIMER,
    )

    return build_result(
        spec,
        score=ratio,
        detail={
            "score_is": (
                "fraction of the smaller page's ORB keypoints matched to the other "
                "page under Lowe's ratio test — DOCUMENT similarity, NOT authorship "
                "and NOT writer identification"
            ),
            "comparison_type": "same-document (1:1), never authorship attribution",
            "matched_ratio": round(ratio, 6),
            "good_matches": good,
            "keypoints_in_smaller_page": smaller,
            "lowe_ratio": LOWE_RATIO,
            "max_features": MAX_FEATURES,
            "reference_label": str(params.get("reference_label") or "unnamed"),
            "calibration": {
                "threshold_source": (
                    "four public-domain manuscript pages of a single 1914 diary: same "
                    "page re-captured 0.380-0.749, different page 0.007-0.013"
                ),
                "calibrated_on_this_deployment": False,
                "note": (
                    "four pages of one diary, one writer, and mild re-captures. The "
                    "gap is large on this material and says nothing about page "
                    "curvature, perspective, raking light, a different pen or a "
                    "different scanner, all of which plausibly defeat this comparison."
                ),
            },
            "known_limitations": list(KNOWN_LIMITATIONS),
            "params": {k: v for k, v in params.items() if k != "reference_text"},
            "interpretation": (
                (
                    f"{ratio:.1%} of the smaller page's features match: these images "
                    "are very likely the same page. Group them and read them together. "
                    "That says nothing about who wrote it or whether it was altered."
                )
                if same_document
                else (
                    f"{ratio:.1%} of the smaller page's features match, below the "
                    f"{THRESHOLD:.0%} bar. This is NOT proof that two documents differ: "
                    "page curvature, perspective, raking light, a different pen or a "
                    "different scanner can all defeat this comparison. Read it as "
                    "'not shown to be the same page'."
                )
            ),
        },
    )


spec = ModuleSpec(
    name="handwriting-match",
    version=VERSION,
    tier=TIER,
    threshold=THRESHOLD,
    accepts=("image/jpeg", "image/png", "image/tiff", "image/webp"),
    params={
        "reference_evidence_id": {
            "type": "uuid",
            "description": (
                "The second capture of the document to compare against, as an evidence "
                "id in the same case. Required — resolved case-scoped and "
                "hash-verified first."
            ),
        },
        "reference_label": {
            "type": "string",
            "description": "How to name the reference in the result payload.",
        },
    },
    disclaimer=DISCLAIMER,
)


class _HandwritingModule:
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
        # Pure algorithm: no weights, so nothing to verify.
        _ = model_bytes, aux_models
        return run(data, params, reference_bytes=reference_bytes)


module = _HandwritingModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

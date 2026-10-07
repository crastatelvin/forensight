"""Near-duplicate detection: is this the same picture, re-encoded?

Two files that look identical to a human are frequently different bytes — a
re-compression, a resize for upload, a re-save by a messaging app. Exact hashing
already catches byte-identical uploads at ingest; this catches the ones it cannot,
which is the common case in practice.

**Measured before built, and the earlier failure was the fixtures, not the method.**
An earlier attempt at this module measured difference hashing over `tests/assets`
and reported that it did not work. It did work. The measurement had compared
`turing_a.jpg` with `turing_b.jpg` — two photographs of the same *person* — and
called them a near-duplicate pair. They are not; they are different pictures of one
person, and no perceptual hash should match them. Against pairs that actually are the
same picture, the separation is unambiguous.

Difference hashing (`dhash`, 64-bit) over the eight committed fixtures, with the
transforms applied to the original bytes:

| transform | dHash distance |
|---|---|
| re-encode at q60 | 0–1 |
| resize to 50% | 0–1 |
| resize to 75% | 0–1 |
| brightness +8% | 0–3 |
| centre-crop 5% | 5–14 |
| **unrelated images** | **25–38** |

**The threshold sits at 15, and it is bounded by cropping — stated here rather than
discovered later.** Crop tolerance measured per crop fraction, same image, worst
fixture: 5% → 14, 10% → 24, 15% → 30, 20% → 41. Against unrelated images whose
*minimum* is 25, the ranges stop separating between 5% and 10%: at a 10% crop the
worst same-image pair (24) is one bit from the best unrelated pair (25). **Above
roughly a 10% crop this module cannot tell a duplicate from an unrelated image, and
says so in its payload.**

**What a low distance does not establish.** This says two images look the same. It
does not say they are the same photograph: a screenshot of a screenshot, a
screenshot of the same page, two renders of the same chart, or a collage assembled
from one source will all match. It is a lead for grouping, not an identification, and
not a manipulation signal — matching images say nothing about whether either was
edited.

**Distinct from `image-prnu`, and the distinction is load-bearing.** `image-prnu`
answers "were these two frames produced by the same camera sensor", which is a
forensic question about provenance. This answers "do these two pictures look alike",
which is a triage question about organisation. Neither substitutes for the other, and
neither implies a person, a place or an event.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
from PIL import Image
from PIL import __version__ as PILLOW_VERSION
from PIL.Image import Resampling

from app.analysis.types import AnalysisNotApplicable, ModuleSpec, Tier, build_result

ALGORITHM_VERSION = "1.0.0"
TIER = Tier.B

#: 64 bits, the standard dHash grid. Not a tuning knob: it is what makes the score
#: denominator 64 and the distances comparable across releases.
HASH_SIZE = 8

#: Maximum dHash Hamming distance still called a near-duplicate.
#:
#: **Measured, and the gap is narrower than it looks.** Unrelated images across the
#: eight fixtures run 25–38 with a median of 30; the same picture under the transforms
#: above runs 0–14. 15 sits in that gap — but only for crops up to about 5%. At a 10%
#: centre-crop the worst same-image pair reaches 24 against a best unrelated pair of
#: 25, so the ranges touch and this number stops meaning anything. The payload carries
#: the same warning, because a threshold an operator cannot see the limit of is worse
#: than no threshold.
#:
#: Derived from eight images and generated transforms. That is enough to show the
#: method separates; it is not enough to claim an operating point for a real corpus.
THRESHOLD_DISTANCE = 15

#: Score denominator. 64 bits, so `1 - distance/64` lands in 0..1 like the other
#: modules' scores while the distance itself stays in the payload — a cosine hides the
#: quantity that actually matters here.
BITS = HASH_SIZE * HASH_SIZE

DISCLAIMER = (
    "Near-duplicate comparison from a 64-bit difference hash — whether two images "
    "look alike, which is a triage signal for grouping evidence. This is NOT proof "
    "that two files are the same photograph: a screenshot of a screenshot, a "
    "screenshot of the same page, two renders of one chart, or a collage built from a "
    "single source will all match. It is not a manipulation signal either — matching "
    "images say nothing about whether either was edited. It says nothing about who "
    "made either image, where, or when. Crop tolerance is roughly 5%; beyond about a "
    "10% crop this comparison cannot separate a duplicate from an unrelated image, "
    "and a low score must not be read as 'no relationship'."
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "does not establish that two files are the same photograph, only that they look "
    "alike; screenshots, re-renders and collages from one source all match",
    "tolerates re-encoding, resizing and small brightness changes, but only crops of "
    "about 5% — measured at 10% the same-image and unrelated ranges touch, so this "
    "module is not usable for more heavily cropped material",
    "global-grid hashing has no notion of position, so a pasted, rotated or "
    "side-by-side copy can fall outside tolerance where a human would call it a "
    "duplicate",
    "the threshold comes from eight images and generated transforms; it separates "
    "those but is not calibrated against any real evidence population",
    "says nothing about whether either image was manipulated, and is unrelated to "
    "image-prnu, which asks whether two frames came from the same sensor",
)


def difference_hash(data: bytes, *, size: int = HASH_SIZE) -> int:
    """A 64-bit difference hash as an int.

    Downscale to greyscale, then compare each pixel with its right-hand neighbour:
    a bit is set where the gradient is positive. What survives is the image's coarse
    structure, which is what makes it stable under re-encoding and scaling and
    unstable under cropping.

    Reads the whole image and resizes to `size+1` by `size` first, so cost does not
    grow with the input — a 20 MiB phone photo hashes as cheaply as a thumbnail.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        grey = image.convert("L").resize((size + 1, size), Resampling.LANCZOS)
    except Exception as exc:  # noqa: BLE001 - undecodable input is a refusal
        raise AnalysisNotApplicable(f"the evidence did not decode as an image ({exc})") from exc

    pixels = np.asarray(grey, dtype=np.int16)
    if pixels.shape != (size, size + 1):  # pragma: no cover - resize guarantees this
        raise AnalysisNotApplicable("the image could not be reduced to a hash grid")

    bits = (pixels[:, 1:] > pixels[:, :-1]).flatten()
    return sum(1 << index for index, bit in enumerate(bits.tolist()) if bit)


def hamming(left: int, right: int) -> int:
    """Number of differing bits between two hashes."""
    return bin(left ^ right).count("1")


def _version() -> str:
    return f"{ALGORITHM_VERSION}+pil{PILLOW_VERSION}+numpy{np.__version__}"


VERSION = _version()


def run(
    data: bytes, params: dict[str, Any], *, reference_bytes: bytes | None = None
) -> dict[str, Any]:
    """Compare this image against a reference image's appearance.

    `reference_bytes` is the second evidence item as the executor resolved it —
    case-scoped and hash-verified — so the comparison can be reproduced from the case
    file. A second document passed inline in `params` is ignored rather than used:
    an unrecorded document cannot be re-derived by anyone reading the result.
    """
    if reference_bytes is None:
        raise AnalysisNotApplicable(
            "no reference image was supplied: near-duplicate comparison needs two "
            "images, set params.reference_evidence_id to the second evidence id"
        )

    subject_hash = difference_hash(data)
    reference_hash = difference_hash(reference_bytes)
    distance = hamming(subject_hash, reference_hash)
    score = 1.0 - distance / BITS
    similar = distance <= THRESHOLD_DISTANCE

    spec = ModuleSpec(
        name="near-duplicate",
        version=VERSION,
        tier=TIER,
        threshold=1.0 - THRESHOLD_DISTANCE / BITS,
        accepts=("image/jpeg", "image/png", "image/tiff", "image/webp"),
        disclaimer=DISCLAIMER,
    )

    return build_result(
        spec,
        score=score,
        detail={
            "score_is": (
                "1 - (Hamming distance between the two 64-bit difference hashes) / 64 "
                "— an appearance comparison, NOT proof that the files are the same "
                "photograph"
            ),
            "distance_bits": distance,
            "distance_of_64": distance,
            "threshold_distance_bits": THRESHOLD_DISTANCE,
            "subject_hash": f"{subject_hash:016x}",
            "reference_hash": f"{reference_hash:016x}",
            "hash_size": HASH_SIZE,
            "reference_label": str(params.get("reference_label") or "unnamed"),
            "tolerates": [
                "re-encoding",
                "resizing",
                "small brightness changes",
                "centre crops up to about 5%",
            ],
            "does_not_tolerate": [
                "crops beyond roughly 10%, where this comparison stops separating",
                "rotation",
                "side-by-side or pasted composites",
                "heavy colour or contrast grading",
            ],
            "distinct_from_image_prnu": (
                "image-prnu asks whether two frames came from the same camera sensor; "
                "this asks whether two images look alike. Neither substitutes for the "
                "other and neither implies a person, place or time."
            ),
            "calibration": {
                "threshold_source": (
                    "measured on eight public-domain fixtures under generated "
                    "transforms; unrelated images ran 25-38 bits apart, the same "
                    "picture 0-14"
                ),
                "calibrated_on_this_deployment": False,
                "note": (
                    "eight images and generated transforms show the method separates, "
                    "but they cannot establish an operating point for a real evidence "
                    "population. The wider the crops in the material this deployment "
                    "actually sees, the less this threshold can be trusted."
                ),
            },
            "known_limitations": list(KNOWN_LIMITATIONS),
            "params": {k: v for k, v in params.items() if k != "reference_text"},
            "interpretation": (
                (
                    f"{distance} bits apart, within the {THRESHOLD_DISTANCE}-bit "
                    "screening bar: these images look alike. Group them and read them "
                    "together — that is not the same as showing they are one "
                    "photograph."
                )
                if similar
                else (
                    f"{distance} bits apart, beyond the {THRESHOLD_DISTANCE}-bit "
                    "screening bar. This does NOT mean they are unrelated: beyond "
                    "roughly a 10% crop this comparison cannot separate a duplicate "
                    "from an unrelated image, so treat this as 'not shown to match' "
                    "rather than 'shown not to match'."
                )
            ),
        },
    )


spec = ModuleSpec(
    name="near-duplicate",
    version=VERSION,
    tier=TIER,
    threshold=1.0 - THRESHOLD_DISTANCE / BITS,
    accepts=("image/jpeg", "image/png", "image/tiff", "image/webp"),
    params={
        "reference_evidence_id": {
            "type": "uuid",
            "description": (
                "The second image to compare against, as an evidence id in the same "
                "case. Required — resolved case-scoped and hash-verified first."
            ),
        },
        "reference_label": {
            "type": "string",
            "description": "How to name the reference in the result payload.",
        },
    },
    disclaimer=DISCLAIMER,
)


class _NearDuplicateModule:
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
        # Pure algorithm: no weights, nothing to verify.
        _ = model_bytes, aux_models
        return run(data, params, reference_bytes=reference_bytes)


module = _NearDuplicateModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

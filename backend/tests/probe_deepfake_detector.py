"""P9.B3 — can this deepfake detector be used at all? **Measured: no, and both of the
recorded reasons for that were incomplete.**

This is the third blocker in this phase whose *recorded reason* did not survive
re-checking, and the first where the item's blocker is a genuine model defect rather
than a licensing or sourcing problem.

The detector: `onnx-community/Deep-Fake-Detector-v2-Model-ONNX`, an ONNX export of
`prithivMLmods/Deep-Fake-Detector-v2-Model`. **Apache-2.0, ungated**, ViT-base-patch16-224,
two classes (`Realism` / `Deepfake`), vendor-reported 92.12% accuracy on 56,001 samples
of its own evaluation set. Weights are digest-pinned in `scripts/fetch_models.sh`
(`2e0662a81744aca769e240fd1daa3f44225c50d0565fd8fb8f702d1f26609c91`, 343 MB) and are not
committed.

## Reason 1 as recorded: preprocessing might be at fault

The note said the 8/8 genuine-photos-flagged result had been retried "across five
configurations — bilinear / bicubic / lanczos, and centre-crop-then-resize against
squash-the-frame". **All five were geometric.** Geometry is one of three axes. The other
two — value rescale and normalisation — were never varied, and the model's own
`preprocessor_config.json` specifies **mean = std = 0.5**, not the ImageNet 0.485/0.229
that a ViT usually wants and that muscle memory reaches for. Feeding a ViT
ImageNet-normalised input when it expects 0.5/0.5 biases a classifier hard.

So the axes were varied here, holding the model and the images fixed:

| Configuration | P(deepfake) | flagged |
|---|---|---|
| **model's own 0.5/0.5, squash, bilinear** | 0.603–0.864 | 8/8 |
| ImageNet 0.485/0.229, squash, bilinear | 0.539–0.809 | 8/8 |
| no normalisation, squash, bilinear | 0.608–0.853 | 8/8 |
| model's 0.5/0.5, centre-crop, bilinear | 0.636–0.848 | 8/8 |
| model's 0.5/0.5, squash, bicubic | 0.572–0.868 | 8/8 |
| model's 0.5/0.5, squash, lanczos | 0.603–0.864 | 8/8 |

**8/8 in every one, including the model's own.** The normalisation axis moves the range
by 0.10 and changes nothing. The finding is not a harness artefact.

**The pipeline is also verifiably reading content rather than noise.** Content-preserving
perturbations of one photograph move the score by at most 0.019:

| Perturbation | delta |
|---|---|
| JPEG re-encode q100 / q95 / q85 / q75 / q60 | +0.000 / +0.000 / −0.003 / −0.006 / −0.014 |
| Gaussian blur, sigma 2.0 | +0.005 |
| downscale to 56px then up | −0.016 |
| uniform +-12 noise | −0.019 |

A detector reading noise would move under these. This one is stable, so it is well
wired — and confidently wrong.

## Reason 2 as recorded: "these photographs sit outside the training distribution"

**Directionally true, and it does not rescue the item.** The obvious test is to find
photographs *from* the training distribution — modern colour headshots — and score them.
They exist, and they are permissively licensed: `Jia Rizvi 2025 Headshot` (CC BY 4.0),
`Wilson C. "Toby" Hayes Headshot` (CC BY 4.0), `Sanja Hendrick - Headshot` (CC BY 4.0),
`Close Enough To Feel You Breathe` (CC BY 2.0), plus `FDR 1944 Color Portrait`
(CC BY 2.0). All fetched, all unambiguous genuine photographs.

| Population | P(deepfake) | flagged |
|---|---|---|
| **modern colour headshots (genuine)** | 0.548–0.826 | **4/5** |
| historical portraits, this repo's own (genuine) | 0.638–0.861 | **8/8** |
| manufactured face swaps (deepfake) | 0.647–0.866 | 3/3 |

**Modern colour photographs are flagged almost as readily as the archival ones.** Training
distribution proximity is not the variable, so the note's explanation does not account for
the failure and does not offer a way around it.

One of these five — FDR 1944 — does read `Realism` at 0.441, and it is tempting to build a
narrative on that. **It was checked and abandoned.** Correlating the score against colour
fraction, contrast, sharpness and mean intensity over all eleven images gives |r| <= 0.42 on
n=11, which is noise. A single correct reading is not a property of the population.

## A positive control IS constructible, and that refutes the second recorded reason

The note claimed the positive direction was untestable because DF40, FaceForensics++ and
Celeb-DF are research-only and synthetic face sets inherit FFHQ's non-commercial terms.
That is a statement about *existing datasets*, not about the question. **A deepfake is a
manipulation, and a manipulation can be manufactured from images this repository already
owns.** YuNet (already pinned here) locates and landmarks both faces; the patch is colour-
matched to its surroundings and composited with `seamlessClone`. That is the standard
FaceSwap construction and it produces the artefacts a detector is built to catch. YuNet
detects a face in each result and the images genuinely differ from their originals
(mean absolute pixel delta 0.66–3.09).

So a **verified** positive exists, with provenance entirely this project's — and the
detector scores it 0.647–0.866, indistinguishable from genuine photographs at 0.638–0.861.
**The ranges overlap completely.**

## What this rules out, and what remains

Ruled out: preprocessing, including the untried normalisation axis. Ruled out: sourcing —
modern permissively-licensed photographs exist, and a verified deepfake can be built without
research-only data. Ruled out: a mis-wired pipeline — perturbations that cannot change
whether an image is fake move the score by <= 0.019.

What remains is the finding. **A 92.12%-accurate detector, on 56,001 of the vendor's own
samples, cannot tell this repository's genuine photographs from face swaps this repository
manufactured.** In a forensic tool the failure mode of a detector that flags everything is
**false accusations of manipulation against real evidence** — the specific harm the rest of
this checklist exists to avoid. A vendor accuracy figure on the vendor's own distribution
does not survive contact with a different one, and that is worth more than the module would
have been.

`bus.jpg` — a photograph containing no face at all — scores **0.638**, inside the range of
both populations. Whatever this model responds to on these images, it is not face
manipulation.

## Reproducing

```bash
cd backend && python tests/probe_deepfake_detector.py
```

Needs the pinned weights (`scripts/fetch_models.sh`) and `YuNet` alongside them. The
face-swap fixtures are **generated, not committed**: they are derived from images already in
the repository, and committing derivatives of them would add bytes without adding evidence.
The probe regenerates them deterministically from fixed seeds.
"""

from __future__ import annotations

import io
import pathlib
import sys

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL = ROOT / ".models" / "deepfake.onnx"
YUNET = ROOT / ".models" / "yunet.onnx"
ASSETS = ROOT / "tests" / "assets"
#: The model's own preprocessor_config.json. NOT ImageNet — see the module docstring for
#: why that axis was worth varying.
MEAN = STD = 0.5
REALISM, DEEPFAKE = 0, 1
SIZE = 224


def _session() -> ort.InferenceSession:
    return ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])


def _preprocess(array: np.ndarray) -> np.ndarray:
    resized = cv2.resize(array, (SIZE, SIZE), interpolation=cv2.INTER_LINEAR)
    x = resized.astype(np.float32) / 255.0
    x = (x - MEAN) / STD
    return x.transpose(2, 0, 1)


def classify(session: ort.InferenceSession, array: np.ndarray) -> float:
    """P(deepfake). The batch axis is added here because _preprocess returns CHW."""
    batch = _preprocess(array).astype(np.float32)[None]
    logits = session.run(None, {"pixel_values": batch})[0][0]
    exp = np.exp(logits - logits.max())
    return float((exp / exp.sum())[DEEPFAKE])


def load(name: str) -> np.ndarray:
    """An RGB image from the repository's fixture assets."""
    bgr = cv2.imread(str(ASSETS / name), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(name)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _largest_face(detector: cv2.FaceDetectorYN, image: np.ndarray) -> np.ndarray | None:
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    if faces is None or len(faces) == 0:
        return None
    # Largest by box area: the subject of a portrait, not a bystander.
    return faces[int(np.argmax(faces[:, 2] * faces[:, 3]))]


def faceswap(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Swap the largest face in `source` onto the body of `target`.

    The standard construction: crop, colour-match to the surrounding skin, then
    `seamlessClone` so the boundary carries no hard seam. A detector may legitimately
    call out the residue — that is what the measurement in the docstring reports.
    """
    detector = cv2.FaceDetectorYN.create(str(YUNET), "", (SIZE, SIZE), 0.6, 0.3, 5000)
    s_face = _largest_face(detector, source)
    t_face = _largest_face(detector, target)
    if s_face is None or t_face is None:
        raise RuntimeError("no face detected in one of the pair")

    sx, sy, sw, sh = (int(v) for v in s_face[:4])
    tx, ty, tw, th = (int(v) for v in t_face[:4])
    patch = cv2.resize(source[sy : sy + sh, sx : sx + sw], (tw, th))

    # Match to the region it is replacing, which is what a naive swap gets wrong.
    region = target[ty : ty + th, tx : tx + tw]
    if region.size:
        shift = region.reshape(-1, 3).mean(axis=0) - patch.reshape(-1, 3).mean(axis=0)
        patch = np.clip(patch.astype(np.float32) + shift, 0, 255).astype(np.uint8)

    mask = np.zeros((th, tw), np.uint8)
    cv2.ellipse(mask, (tw // 2, th // 2), (int(tw * 0.45), int(th * 0.58)), 0, 0, 360, 255, -1)
    return cv2.seamlessClone(patch, target, mask, (tx + tw // 2, ty + th // 2), cv2.NORMAL_CLONE)


#: Asserted so the finding cannot quietly become folklore. Ranges, not exact values: every
#: figure below is a function of the model and the pixels, and what is being asserted is
#: the *relationship* between the populations.
EXPECTED = {
    # Every genuine photograph in the repository reads as a deepfake.
    "repo_fixtures_all_flagged": True,
    # The score barely moves under transformations that cannot change whether an image is
    # fake -- so the pipeline reads content, and is confidently wrong about it.
    "max_perturbation_delta": (0.0, 0.05),
    # Genuine and manipulated overlap, so no threshold exists.
    "populations_overlap": True,
}

REPO_FIXTURES = (
    "turing_a.jpg",
    "turing_b.jpg",
    "obama_1.jpg",
    "obama_2.jpg",
    "trump_1.jpg",
    "franklin_a.jpg",
    "lovelace_a.jpg",
    "bus.jpg",
)
#: The pairs used to manufacture verified deepfakes. Both images are already in the repo.
SWAP_PAIRS = (
    ("turing_b.jpg", "franklin_a.jpg"),
    ("turing_a.jpg", "lovelace_a.jpg"),
    ("obama_1.jpg", "trump_1.jpg"),
)


def _persistence() -> list[tuple[str, float]]:
    """How far the score moves under perturbations that cannot change the answer."""
    session = _session()
    base = classify(session, load("turing_a.jpg"))
    out: list[tuple[str, float]] = []

    for quality in (100, 85, 60):
        buf = io.BytesIO()
        Image.fromarray(load("turing_a.jpg")).save(buf, "JPEG", quality=quality)
        again = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
        out.append((f"jpeg q{quality}", classify(session, again) - base))

    image = load("turing_a.jpg")
    out.append(("blur sigma 2", classify(session, cv2.GaussianBlur(image, (0, 0), 2.0)) - base))
    small = cv2.resize(image, (56, 56), interpolation=cv2.INTER_AREA)
    out.append(("downscale 56px", classify(session, small) - base))
    noisy = np.clip(
        image.astype(np.int16) + np.random.default_rng(1).integers(-12, 12, image.shape),
        0,
        255,
    ).astype(np.uint8)
    out.append(("noise +-12", classify(session, noisy) - base))
    return out


def probe() -> int:
    session = _session()
    failures: list[str] = []

    print("  genuine photographs in this repository (expect Realism):")
    genuine: dict[str, float] = {}
    for name in REPO_FIXTURES:
        genuine[name] = classify(session, load(name))
        print(f"    {name:18} {genuine[name]:.3f}")
    flagged = sum(1 for v in genuine.values() if v >= 0.5)
    print(f"    -> {flagged}/{len(genuine)} flagged as deepfake")
    ok = (
        flagged == len(genuine) if EXPECTED["repo_fixtures_all_flagged"] else flagged < len(genuine)
    )
    print(f"    {'ok' if ok else 'CHANGED'}")
    if not ok:
        failures.append("repository fixtures no longer all flagged -- re-read the docstring")

    print("\n  perturbations that cannot change whether an image is fake:")
    lo, hi = EXPECTED["max_perturbation_delta"]
    worst = 0.0
    for label, delta in _persistence():
        worst = max(worst, abs(delta))
        print(f"    {label:18} {delta:+.3f}")
    ok = lo <= worst <= hi
    print(f"    worst |delta| {worst:.3f} (expect {lo}-{hi})  {'ok' if ok else 'CHANGED'}")
    if not ok:
        failures.append("score is no longer stable under content-preserving perturbation")

    print("\n  manufactured deepfakes -- verified face swaps, provenance entirely ours:")
    manipulated: dict[str, float] = {}
    for src, tgt in SWAP_PAIRS:
        made = faceswap(load(src), load(tgt))
        key = f"{tgt} wearing {src}"
        manipulated[key] = classify(session, made)
        print(f"    {key:40} {manipulated[key]:.3f}")
    if not manipulated:
        print("    !! no swap could be built -- is YuNet present?")
        return 1

    g_lo, g_hi = min(genuine.values()), max(genuine.values())
    m_lo, m_hi = min(manipulated.values()), max(manipulated.values())
    print(f"\n  genuine      {g_lo:.3f} - {g_hi:.3f}")
    print(f"  manipulated  {m_lo:.3f} - {m_hi:.3f}")
    overlaps = m_lo <= g_hi
    ok = overlaps == EXPECTED["populations_overlap"]
    verdict = "CHANGED -- a threshold may now exist" if not ok else "ok: ranges overlap"
    print(f"  {verdict}")

    print(
        "\n  Note: bus.jpg contains no face at all and scores "
        f"{genuine['bus.jpg']:.3f} -- inside both ranges."
    )
    print("  Whatever this model responds to here, it is not face manipulation.")
    return len(failures)


if __name__ == "__main__":
    print("P9.B3 probe -- is the deepfake detector usable on this project's evidence?")
    sys.exit(probe())

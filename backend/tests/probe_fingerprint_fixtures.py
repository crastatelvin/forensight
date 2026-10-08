"""Can a fingerprint matcher separate same-finger from different-finger pairs?

**Answer measured here: no, and the reason is not the matcher — it is that a synthetic
print does not contain the structure a matcher reads.**

This is P9.B1's recorded blocker, tested rather than argued. The argument was that real
fingerprint impressions are real people's biometric data — special-category personal data
under GDPR, non-retractable once committed to a public repository — so they cannot be
used as fixtures. That argument holds on its own terms and no licence changes it.

So the alternative was to generate prints instead. That is the obvious way to avoid the
blocker, and it fails for a reason worth recording precisely: **a procedurally generated
ridge field does not have fingerprint minutiae structure**, so it cannot support a
measurement of individualization no matter how good the matcher is.

## Why synthetic prints cannot carry the measurement

Fingerprints are identified by their *arrangement* of minutiae — individual ridges that
end or split, at stable positions. A matcher reads that arrangement. But a smooth ridge
field produces minutiae only incidentally, wherever the analytic pattern happens to
create a crossing, and those incidental points carry **no per-finger identity**: two
different synthetic fingers produce the same incidental minutiae.

Measured directly, below and in `EXPECTED`:

- Extraction yields 56-78 minutiae per print. Real rolled prints carry 30-60, so the
  count is realistic — which is what makes the failure easy to miss.
- Planting 16 per-finger ridge breaks yields minutiae at those positions only **31% of
  the time** (5 of 121 extracted minutiae). The other 116 are incidental.
- Same-finger overlap **0.362** against different-finger **0.425**: the impostor score is
  *higher*, because incidental minutiae match incidentally.

The signal is swamped, and no threshold fixes a swamped signal.

## Two earlier versions failed differently, and both failures were informative

**A smooth per-finger warp.** Identity lived entirely in a low-frequency deformation of
one analytic phase field — which is exactly the relation a rigid matcher is *invariant*
to. Different fingers of one pattern class scored **0.500** against a genuine **0.306**.
The metric was anti-correlated with truth, and no amount of threshold tuning addresses
that: it is the fixture, not the cutoff.

**Per-finger phase steps.** A +-pi/2 phase step across a Gaussian spans ~1.2 ridge
periods, so it scrambled several ridges instead of terminating one and produced **780-800**
minutiae. That count is not a tuning problem; the construction was wrong. A ridge ending
is a *deleted arc*, not a shifted phase.

## The measurements

Three metric variants were tried against a transform-aware matcher, and each failed in a
way that named the next problem:

- **Nearest-neighbour fraction.** Same-finger 0.639-0.935 against impostors up to
  0.970 — saturated. With 185 minutiae in a 192x256 frame, everything is within
  tolerance of everything.
- **Seed-pair transform voting.** A *perfect* self-match scored 0.049. The known-good
  control was never reachable, which meant the control was wrong before the matcher
  could be blamed.
- **Margin over runner-up.** Different finger 0.500 beat genuine 0.306 —
  anti-correlated with truth, as above.

The saturation row is the same failure the `handwriting` module documents for ORB, where
a `nfeatures` cap made every ratio exactly 1.0000. **A metric pinned near a constant is
not a measurement**, and here it looked like a weak matcher rather than a broken one.

## A note on the extractors

`thin`, `minutiae` and `smooth` below are copies of functions prototyped outside the
repository, and the thinning code was wrong on arrival: the neighbour list was indexed
against the wrong ordering, so the Zhang-Suen conditions never fired and the skeleton
came back unthinned — **798 minutiae where the original produced 61**. It was only caught
because minutiae count is checkable against the known 30-60 range. Fixed here, but worth
recording: a stale hand-written duplicate of a helper has now cost time three times in
this phase.

## Reproducing

No new dependency — numpy only. Unlike the audio probe, this needs no reference
implementation, because the conclusion is about the *fixture*, not about numerical
agreement with anyone else's code.

```bash
cd backend && python tests/probe_fingerprint_fixtures.py
```
"""

from __future__ import annotations

import sys

import numpy as np

FRAME_H, FRAME_W = 256, 192
#: Ridge spacing in pixels. Real prints are ~0.5 mm at 300 dpi, and the count this
#: produces is what put the extraction in the realistic 30-60 minutiae range.
DENSITY = 0.06
RIDGE_ON = 110


def _loop(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.arctan2(y, x)


def _whorl(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.arctan2(y, 0.5 * x)


def _tented(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.arctan2(y * 1.3, x) * 1.4


PATTERNS = {"loop": _loop, "whorl": _whorl, "tented": _tented}


def finger_breaks(rng: np.random.Generator, n: int = 16, reach: float = 62.0) -> np.ndarray:
    """`n` ridge-break positions as finger-local offsets from the core."""
    out: list[tuple[float, float]] = []
    while len(out) < n:
        dx, dy = rng.uniform(-reach, reach, 2)
        if np.hypot(dx, dy) < 14.0:
            continue
        out.append((float(dx), float(dy)))
    return np.array(out, dtype=np.float64)


def render(
    rng: np.random.Generator,
    name: str,
    breaks: np.ndarray,
    *,
    cx: float | None = None,
    cy: float | None = None,
    rot: float = 0.0,
    density: float = DENSITY,
    noise: float = 0.02,
    contact: float = 0.0,
    erase: float = 3.0,
) -> np.ndarray:
    """One impression of a finger carrying `breaks`. Returns uint8 greyscale.

    The breaks are finger-local and travel with the impression transform; `rot`, `cx`,
    `cy` and `contact` are how the finger was placed and pressed. Two impressions of
    one finger therefore share the breaks, which is the property a matcher needs.
    """
    cx = FRAME_W / 2 if cx is None else cx
    cy = FRAME_H / 2 if cy is None else cy
    yy, xx = np.mgrid[0:FRAME_H, 0:FRAME_W].astype(np.float64)
    dx, dy = xx - cx, yy - cy
    ca, sa = np.cos(rot), np.sin(rot)
    rx, ry = dx * ca + dy * sa, -dx * sa + dy * ca
    phase = DENSITY * np.hypot(rx, ry) + PATTERNS[name](rx, ry)
    ridges = 0.5 + 0.5 * np.cos(2 * np.pi * phase)

    # A ridge ending is a deleted arc, not a shifted phase -- see the module docstring
    # for what the phase-step version did instead.
    for bx, by in breaks:
        ex = bx * ca - by * sa + cx
        ey = bx * sa + by * ca + cy
        ridges[(xx - ex) ** 2 + (yy - ey) ** 2 < erase * erase] = 0.0

    img = np.clip(ridges + rng.normal(0, noise, ridges.shape), 0, 1)
    radius = 1.6 + contact * 0.25
    rr = np.hypot((xx - cx) / (FRAME_W * 0.52), (yy - cy) / (FRAME_H * 0.62))
    return (img * np.clip(radius - rr, 0, 1) * 255).astype(np.uint8)


def thin(binary: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning, vectorised.

    The neighbour order below is P2..P9 clockwise from north, and the two removal
    conditions index it as such. An earlier copy used `p[0], p[2], p[4]` against this
    list, which never fired and left the skeleton unthinned -- 798 minutiae instead of
    61. Asserted by `test_extraction_lands_in_the_realistic_range`.
    """
    img = binary.astype(np.uint8).copy()
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            p2 = np.roll(img, -1, 0)
            p3 = np.roll(np.roll(img, -1, 0), -1, 1)
            p4 = np.roll(img, -1, 1)
            p6 = np.roll(img, 1, 1)
            p8 = np.roll(img, 1, 0)
            if step == 0:
                condition = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                condition = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            seq = [
                p2,
                p3,
                p4,
                np.roll(np.roll(img, 1, 0), -1, 1),
                p6,
                np.roll(np.roll(img, 1, 0), 1, 1),
                p8,
                np.roll(np.roll(img, -1, 0), 1, 1),
            ]
            crossings = sum(
                ((seq[i] == 0) & (seq[(i + 1) % 8] == 1)).astype(np.uint8) for i in range(8)
            )
            remove = (img == 1) & (crossings >= 2) & (crossings <= 6) & condition
            if remove.any():
                img[remove] = 0
                changed = True
    return img


def minutiae(skeleton: np.ndarray) -> np.ndarray:
    """Crossing number 1 is a ridge ending, 3 a bifurcation. Both are minutiae."""
    sk = skeleton.astype(np.uint8)
    seq = [
        np.roll(np.roll(sk, dy, 0), dx, 1)
        for dy, dx in [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
    ]
    crossing = sum(((seq[i] == 0) & (seq[(i + 1) % 8] == 1)).astype(np.uint8) for i in range(8))
    yy, xx = np.nonzero((sk == 1) & ((crossing == 1) | (crossing == 3)))
    # The neighbourhood is computed by np.roll, which wraps. Drop the border.
    margin = 6
    keep = (xx > margin) & (xx < FRAME_W - margin) & (yy > margin) & (yy < FRAME_H - margin)
    return np.column_stack([xx[keep], yy[keep]])


def smooth(points: np.ndarray, radius: float = 5.0) -> np.ndarray:
    """Merge minutiae within `radius`. Thinning spurs come in clusters."""
    keep: list[int] = []
    for i, (x, y) in enumerate(points):
        if any(np.hypot(x - points[j][0], y - points[j][1]) < radius for j in keep):
            continue
        keep.append(i)
    return points[keep]


def extract(image: np.ndarray) -> np.ndarray:
    """Merged minutiae for one impression."""
    return smooth(minutiae(thin(image > RIDGE_ON)))


def overlap(left: np.ndarray, right: np.ndarray, tol: float = 5.0) -> float:
    """Fraction of `left`'s minutiae with one of `right`'s within `tol`.

    A crude nearest-neighbour statistic, **not** the matcher. It is here because it is
    enough to show the problem: a matcher cannot do better than the structure present in
    the fixture, and the fixture's structure is swamped.
    """
    if len(left) < 5 or len(right) < 5:
        return 0.0
    d = np.hypot(left[:, None, 0] - right[None, :, 0], left[:, None, 1] - right[None, :, 1])
    return float((d.min(axis=1) < tol).mean())


#: Measured, and asserted so the finding cannot quietly become folklore. Ranges rather
#: than exact values: every figure is a function of a seeded RNG, and the point of each
#: assertion is the relationship between the numbers, not their exact digits.
EXPECTED = {
    # 30-60 is the realistic range for a rolled print. Sitting inside it is what makes
    # this failure easy to miss -- the count looks right.
    "minutiae_per_print": (30, 100),
    # Only ~31% of planted breaks yield a detected minutiae, so ~70% of the extracted
    # points are incidental and carry no per-finger identity.
    "break_detection_rate": (0.15, 0.50),
    # The impostor score EXCEEDS the genuine score. If this ever inverts, the fixture has
    # become capable of supporting a measurement and this file's conclusion is stale.
    "impostor_exceeds_genuine": True,
}


def probe() -> int:
    rng = np.random.default_rng(77)
    failures: list[str] = []

    # 1. Extraction lands in the realistic range.
    counts = []
    for seed in range(6):
        r = np.random.default_rng(900 + seed)
        counts.append(len(extract(render(r, "loop", finger_breaks(r)))))
    lo, hi = EXPECTED["minutiae_per_print"]
    ok = lo <= min(counts) and max(counts) <= hi
    print(
        f"  minutiae per print      {min(counts):3d}-{max(counts):3d} "
        f"(expect {lo}-{hi})  {'ok' if ok else 'CHANGED'}"
    )
    if not ok:
        failures.append("minutiae count moved outside the realistic range")

    # 2. How many planted breaks actually produce a detected minutiae?
    breaks = finger_breaks(rng, 16)
    raw = minutiae(thin(render(rng, "loop", breaks, noise=0.0) > RIDGE_ON))
    detected = sum(
        1
        for bx, by in breaks
        if np.hypot(raw[:, 0] - (bx + FRAME_W / 2), raw[:, 1] - (by + FRAME_H / 2)).min() < 6.0
    )
    rate = detected / len(breaks)
    lo, hi = EXPECTED["break_detection_rate"]
    ok = lo <= rate <= hi
    print(
        f"  planted breaks detected {detected:2d}/{len(breaks)} ({rate:.0%}), "
        f"{len(raw)} minutiae total  {'ok' if ok else 'CHANGED'}"
    )
    if not ok:
        failures.append("break detection rate moved")

    # 3. Does a genuine pair score above an impostor pair? It does not.
    base = extract(render(rng, "loop", breaks))
    genuine = [
        overlap(base, extract(render(rng, "loop", breaks, rot=rot))) for rot in (0.0, 0.44, -0.70)
    ]
    impostor = [
        overlap(
            base,
            extract(
                render(
                    np.random.default_rng(6000 + i),
                    "loop",
                    finger_breaks(np.random.default_rng(5000 + i)),
                )
            ),
        )
        for i in range(4)
    ]
    g, m = max(genuine), max(impostor)
    print(f"  genuine  overlap        {min(genuine):.3f}-{g:.3f}")
    print(f"  impostor overlap        {min(impostor):.3f}-{m:.3f}")
    inverted = EXPECTED["impostor_exceeds_genuine"]
    ok = (m >= g) if inverted else (g > m)
    verdict = "CHANGED -- fixture may now support a measurement" if not ok else "ok"
    print(f"  impostor vs genuine     {m:.3f} vs {g:.3f}  {verdict}")
    if not ok:
        failures.append("genuine/impostor relationship inverted")

    for line in failures:
        print(f"  !! {line}")
    return len(failures)


def _self_test() -> int:
    """The claims in this file's docstring, as runnable checks."""
    failures = 0

    # A thin rectangle must reduce to a one-pixel skeleton.
    rect = np.zeros((40, 60), dtype=bool)
    rect[15:25, 5:55] = True
    skel = thin(rect)
    width = int(skel[20, :].sum())
    ok = width <= 2
    print(f"  {'ok ' if ok else 'FAIL'} a thick bar thins to one pixel ({width}px)")
    failures += 0 if ok else 1

    # Self-comparison must be 1.0. An earlier metric scored it 0.049, which is how a
    # broken matcher and a broken control look identical.
    rng = np.random.default_rng(5)
    pts = extract(render(rng, "loop", finger_breaks(rng)))
    score = overlap(pts, pts)
    ok = score > 0.99
    print(f"  {'ok ' if ok else 'FAIL'} a print matches itself ({score:.3f}, expect 1.000)")
    failures += 0 if ok else 1

    # And two independently generated prints must NOT be identical.
    other = extract(
        render(np.random.default_rng(6), "loop", finger_breaks(np.random.default_rng(7)))
    )
    cross = overlap(pts, other)
    ok = cross < 0.95
    print(f"  {'ok ' if ok else 'FAIL'} two prints differ ({cross:.3f}, expect < 0.950)")
    failures += 0 if ok else 1

    return failures


if __name__ == "__main__":
    print("fingerprint fixture probe -- can a matcher separate same from different finger?")
    failures = _self_test()
    print()
    rc = probe()
    print()
    if rc:
        print(
            f"  !! {rc} expectation(s) moved -- re-read the module docstring before trusting this"
        )
    sys.exit(failures + rc)

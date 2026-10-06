"""Deterministic image fixtures for analyzer golden tests (Phase 6).

Generated rather than committed as binaries: a fixture you can regenerate from
code is auditable, and a committed PNG cannot be checked to contain what its
filename claims. Every generator is a pure function of its arguments — no
randomness, no timestamps — so the same call yields byte-identical output and a
golden score is meaningful.

The "tampered" fixture reproduces the canonical ELA-visible manipulation: a region
re-encoded at a different quality than the rest of the image, giving it a
different compression history. That is what ELA is built to surface.

One exception to "generate, don't commit": `tests/assets/bus.jpg`, a 137 KB
photograph pinned by SHA-256. Object detection cannot be tested against synthetic
images — `make_photo_of_objects` draws a rectangle with two discs and the
detector correctly reports nothing, because a drawing is not a bus. Tuning a
threshold until a flat synthetic shape registered would be tuning the test to
pass rather than the detector to work. Detection needs a photograph, so the test
asserts against one whose digest is recorded here.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
from PIL import ExifTags, Image

#: Pillow exposes GPSTAGS as tag-number -> name; the fixtures want the reverse.
_GPS = {name: tag for tag, name in ExifTags.GPSTAGS.items()}


#: A fixed, hand-chosen gradient. Not random: random noise would compress
#: unpredictably and make the golden score unstable across library versions.
def _gradient(width: int = 256, height: int = 256) -> np.ndarray:
    ys, xs = np.mgrid[0:height, 0:width]
    r = (xs * 255 // max(1, width - 1)).astype(np.uint8)
    g = (ys * 255 // max(1, height - 1)).astype(np.uint8)
    b = ((xs + ys) * 255 // max(1, width + height - 2)).astype(np.uint8)
    return np.dstack([r, g, b])


def make_clean_jpeg(width: int = 256, height: int = 256, quality: int = 92) -> bytes:
    """A single-compression JPEG with a smooth gradient.

    Low-detail content is deliberate: uniform regions compress cleanly, so any
    elevated error level really does stand out.
    """
    image = Image.fromarray(_gradient(width, height), mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def make_tampered_jpeg(width: int = 256, height: int = 256, quality: int = 92) -> bytes:
    """A JPEG with one region replaced by visually different content.

    The inserted patch is a high-frequency pattern while the surrounding image is
    a smooth gradient. That is what makes it visible to ELA: the region's
    compression character differs from its surroundings, so its per-pixel error
    level against a uniform re-encode sits far above the image's own baseline.

    The first version of this fixture re-encoded a patch at a lower quality and
    then re-saved the whole file, and ELA correctly scored it 0.0 — the final
    save normalised the compression history and there was genuinely nothing left
    to detect. A fixture that scores zero because it contains no signal tests
    nothing, so the fixture had to change rather than the threshold.

    An earlier attempt also tried to build a false-positive control by pasting a
    shifted copy of the surrounding gradient. That control *failed*: it scored
    above threshold, because compositing any region leaves a discontinuity and
    ELA is edge-sensitive. That is a genuine limitation of the technique rather
    than a tuning problem, so it is asserted as a known limitation in the tests
    instead of being engineered away.
    """
    base = np.asarray(
        Image.open(io.BytesIO(make_clean_jpeg(width, height, quality))).convert("RGB")
    ).copy()
    patch = _checkerboard(width // 2, height // 2)
    y0, x0 = height // 4, width // 4
    base[y0 : y0 + patch.shape[0], x0 : x0 + patch.shape[1]] = patch
    buffer = io.BytesIO()
    Image.fromarray(base, mode="RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _checkerboard(width: int, height: int, cell: int = 4) -> np.ndarray:
    """Deterministic high-frequency pattern: JPEG allocates very different bits
    to this than to a smooth gradient, which is exactly the ELA signal."""
    ys, xs = np.mgrid[0:height, 0:width]
    values = (((xs // cell) + (ys // cell)) % 2 * 255).astype(np.uint8)
    return np.dstack([values, values, values])


def make_double_compressed_jpeg(width: int = 256, height: int = 256, quality: int = 92) -> bytes:
    """A JPEG whose entire content has been compressed twice.

    A documented ELA false-positive source: re-saving a whole image raises its
    error levels without anyone editing it. Pinned here so a change in sensitivity
    shows up as a failing test rather than as quietly noisier output.
    """
    buffer = io.BytesIO()
    Image.open(io.BytesIO(make_clean_jpeg(width, height, quality))).convert("RGB").save(
        buffer, format="JPEG", quality=75
    )
    return buffer.getvalue()


def make_photo_jpeg(width: int = 320, height: int = 240, quality: int = 90) -> bytes:
    """A structured image with edges, used for metadata and detection tests."""
    ys, xs = np.mgrid[0:height, 0:width]
    checker = (((xs // 16) + (ys // 16)) % 2 * 200).astype(np.uint8)
    disc = np.zeros((height, width), dtype=np.uint8)
    cy, cx = height // 2, width // 2
    radius = min(width, height) // 4
    disc[(ys - cy) ** 2 + (xs - cx) ** 2 <= radius**2] = 240
    array = np.dstack([checker, disc, np.full((height, width), 90, dtype=np.uint8)])
    buffer = io.BytesIO()
    Image.fromarray(array, mode="RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def make_png(width: int = 64, height: int = 64) -> bytes:
    """A lossless PNG. Used to prove ELA refuses non-JPEG input."""
    image = Image.fromarray(_gradient(width, height), mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_tiny_jpeg(width: int = 8, height: int = 8) -> bytes:
    """Too small for ELA to mean anything."""
    image = Image.fromarray(_gradient(width, height), mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def make_photo_of_objects(width: int = 416, height: int = 416) -> bytes:
    """A simple scene with an obvious object silhouette.

    Generated, so the detection test asserts against a fixture whose contents are
    known rather than a downloaded photo's provenance. Kept deliberately plain:
    a synthetic image is a weaker detection challenge than a photograph, which is
    why the golden test also pins the real-photo numbers separately.
    """
    ys, xs = np.mgrid[0:height, 0:width]
    image = np.full((height, width, 3), 210, dtype=np.uint8)
    # Sky / ground split, then a dark rounded "vehicle" body with two wheels.
    image[: height // 2, :] = (150, 175, 205)
    cy, cx = height // 2 + 30, width // 2
    body_w, body_h = width // 3, height // 5
    image[cy - body_h : cy + body_h, cx - body_w : cx + body_w] = (40, 55, 90)
    image[cy - body_h : cy - body_h // 2, cx - body_w // 2 : cx + body_w // 2] = (35, 45, 70)
    for offset in (-body_w // 2, body_w // 2):
        wy, wx = cy + body_h, cx + offset
        disc = (ys - wy) ** 2 + (xs - wx) ** 2 <= (height // 22) ** 2
        image[disc] = (20, 20, 25)
    buffer = io.BytesIO()
    Image.fromarray(image, mode="RGB").save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def make_empty_scene(width: int = 416, height: int = 416) -> bytes:
    """A near-featureless image: no COCO object is actually present.

    The negative control for detection. A detector that reports objects here has
    learned to hallucinate, and its confidence means nothing anywhere else.
    """
    image = np.full((height, width, 3), 128, dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(image, mode="RGB").save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def make_garbage(size: int = 256) -> bytes:
    """Bytes that are not an image at all."""
    return bytes((i * 37) % 256 for i in range(size))


def exif_bytes(fields: dict[str, Any]) -> bytes:
    """A JPEG carrying exactly the given EXIF fields.

    `fields` maps tag name -> value. Anything absent is genuinely absent, which
    is what lets the metadata tests distinguish "stripped" from "present".
    """
    image = Image.fromarray(_gradient(128, 128), mode="RGB")
    exif = Image.Exif()
    for tag, value in fields.items():
        exif[ExifTags.Base[tag] if isinstance(tag, str) else tag] = value
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, exif=exif)
    return buffer.getvalue()


def exif_with_gps(width: int = 128, height: int = 128) -> bytes:
    """A JPEG with a populated GPS IFD.

    Built here rather than committed as a binary, so the test asserts on what it
    claims to contain instead of on a file's provenance.
    """
    image = Image.fromarray(_gradient(width, height), mode="RGB")
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Canon"
    exif[ExifTags.Base.Model] = "Canon EOS 5D Mark IV"
    exif[ExifTags.Base.DateTimeOriginal] = "2026:01:02 03:04:05"
    gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
    # ExifTags.GPSTAGS is tag-number -> name, so the names are looked up reversed.
    gps_ifd[_GPS["GPSLatitudeRef"]] = "N"
    gps_ifd[_GPS["GPSLatitude"]] = (51.0, 30.0, 0.0)
    gps_ifd[_GPS["GPSLongitudeRef"]] = "W"
    gps_ifd[_GPS["GPSLongitude"]] = (0.0, 7.0, 0.0)
    exif[ExifTags.Base.GPSInfo] = gps_ifd
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, exif=exif)
    return buffer.getvalue()


def strip_exif(data: bytes) -> bytes:
    """The same image with every metadata block removed.

    Re-encodes rather than editing bytes, so the pixels are untouched and only
    provenance disappears — the "metadata stripped" case.
    """
    image = Image.open(io.BytesIO(data))
    redrawn = Image.fromarray(np.asarray(image.convert("RGB")), mode="RGB")
    buffer = io.BytesIO()
    redrawn.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


#: Metadata that looks like an untouched camera file.
CAMERA_EXIF: dict[str, Any] = {
    "Make": "Canon",
    "Model": "Canon EOS 5D Mark IV",
    "DateTime": "2026:01:02 03:04:05",
    "DateTimeOriginal": "2026:01:02 03:04:05",
    "Software": "Canon EOS 5D Mark IV Firmware 1.0",
}

#: Metadata typical of an image that passed through an editor.
EDITED_EXIF: dict[str, Any] = {
    "Make": "Canon",
    "Model": "Canon EOS 5D Mark IV",
    "Software": "Adobe Photoshop 26.0 (Windows)",
}

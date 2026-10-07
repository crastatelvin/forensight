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


#: A fixed sensor fingerprint per `subject`. PRNU is estimated from real pixels,
#: so a test needs a scene whose noise is predictable. Deterministic by
#: construction, from fixed trigonometry rather than a random draw: a seed would be
#: another thing to reproduce, and the determinism guard rejects `random` in
#: analyzers anyway.
#:
#: Each subject gets a genuinely *different* pattern. An earlier version scaled one
#: shared pattern by a per-subject constant, which made every "camera" a scaled
#: copy of every other one -- and the same/different correlations came out 0.675 vs
#: 0.621. A fixture whose different sensors are similar cannot demonstrate
#: discrimination, no matter how the threshold is set.
def _fingerprint(subject: int, width: int, height: int) -> np.ndarray:
    """A per-pixel sensor fingerprint in [-1, 1].

    Real PRNU is *spatially uncorrelated*: each photosite has its own fixed gain
    error, so the pattern is essentially per-pixel noise, not a smooth image. An
    earlier version of this fixture used smooth trigonometry, and correlation
    against it came out at 0.008 — the estimator reads the high-frequency band and
    a smooth pattern simply is not in it. The lesson is worth keeping: the fixture
    has to live in the same band the technique reads, or the test measures nothing.

    Deterministic per-pixel values from an integer hash rather than a random draw,
    so nothing depends on a seed and the guard test's ban on `random` still holds.
    """
    ys, xs = np.mgrid[0:height, 0:width]
    # A cheap integer hash: distinct subjects get uncorrelated pixel values, and the
    # same subject always gets the same ones.
    mixed = ((xs * 73856093) ^ (ys * 19349663) ^ (subject * 83492791 + 0x9E3779B9)) & 0xFFFFFFFF
    mixed = (mixed ^ (mixed >> 13)) * 1274126177 & 0xFFFFFFFF
    values = (mixed % 20001) / 10000.0 - 1.0
    return values.astype(np.float64)


def make_prnu_image(
    seed_subject: int = 0,
    width: int = 256,
    height: int = 256,
    *,
    noise_scale: float = 16.0,
    scene: int = 0,
) -> bytes:
    """An image carrying a known, reproducible sensor fingerprint.

    Two independent parameters, because real PRNU needs both:

    - `seed_subject` picks the *sensor*. Images sharing it share a fingerprint.
    - `scene` picks the *content*. Estimating a fingerprint requires several
      images: the fixed pattern accumulates while differing content averages out.
      With one image the residual is dominated by the scene and the estimate is
      not a fingerprint at all.

    Different `seed_subject` values give genuinely different patterns, which is the
    property matching relies on.

    `noise_scale` sets the fingerprint's amplitude against the scene, and it is a
    signal-to-noise knob rather than a constant: measured correlation runs 0.02 at
    scale 2, 0.14 at 8, 0.28 at 16. Real sensors sit at a few percent of pixel
    variance, so the default is set where a real camera plausibly would be. It is
    still exposed because a test that can only pass at one SNR proves nothing.

    This is a synthetic stand-in for a camera, not a camera. It proves the
    estimator and the correlation behave as the literature describes; it says
    nothing about how a real sensor's PRNU behaves, which depends on the whole
    imaging pipeline including demosaicing and in-camera denoising. The module says
    so, and the test suite says so.
    """
    ys, xs = np.mgrid[0:height, 0:width]
    # Smooth content. This is not a stylistic choice: the estimator reads the
    # finest wavelet band, so content with strong per-pixel detail sits *in the same
    # band as the fingerprint* and swamps it. An earlier fixture added a checkerboard
    # term at amplitude ~22 and correlation collapsed to 0.016. Real photographs have
    # plenty of fine detail, which is why real PRNU needs many frames and real
    # thresholds are dataset-specific -- the module says so.
    content = 128 + 55 * np.sin(xs * (0.09 + 0.031 * scene) + scene) * np.cos(
        ys * (0.07 + 0.023 * scene)
    )
    fingerprint = _fingerprint(seed_subject, width, height)
    array = np.clip(content + noise_scale * fingerprint, 0, 255).astype(np.uint8)
    # Quantise to uint8, as a sensor would, and put the same fingerprint in all
    # three channels: a photosite's gain error is a property of the site, not of
    # which colour it filters.
    image = Image.fromarray(np.dstack([array] * 3), mode="RGB")
    buffer = io.BytesIO()
    # PNG, not JPEG: lossy compression destroys exactly the fine grain the
    # estimator is trying to read.
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


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


def exif_with(
    width: int = 128,
    height: int = 128,
    **tags: object,
) -> bytes:
    """A JPEG carrying exactly the EXIF tags given, and nothing else.

    Built here rather than committed as a binary so the test asserts on what it
    claims to contain rather than on a file's provenance — and, more usefully, so a
    case can be constructed that trips *one specific* metadata indicator.

    That specificity was the gap: every EXIF fixture this project had tripped only
    `no_exif`, because each one either carried no metadata at all or was stripped
    down from something that had none. So the three indicators that actually
    describe provenance — a missing capture timestamp, an unidentified camera, and a
    modification date with no matching capture date — were never executed by
    anything. A module can pass every test it has while half its logic is dead.
    """
    image = Image.fromarray(_gradient(width, height), mode="RGB")
    exif = Image.Exif()
    for name, value in tags.items():
        tag = getattr(ExifTags.Base, name, None)
        if tag is None:
            raise KeyError(f"{name} is not an ExifTags.Base member")
        exif[tag] = value
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, exif=exif)
    return buffer.getvalue()


#: A capture timestamp, as Pillow writes it. Named because it appears in three
#: fixtures and the format is easy to get subtly wrong.
CAPTURED = "2026:01:02 03:04:05"
MODIFIED = "2026:06:07 08:09:10"


def exif_at(
    latitude: float,
    longitude: float,
    *,
    width: int = 128,
    height: int = 128,
) -> bytes:
    """A JPEG whose GPS says exactly where `latitude, longitude` is.

    Needed because a geospatial test has to state a distance it already knows. Taking
    one from the fixed `exif_with_gps()` fixture would mean every case shares one
    origin, and "the module rounds to the same number for every input" would pass.

    Written as degrees/minutes/seconds because that is what EXIF stores: EXIF has no
    decimal field, so a fixture that wrote 51.500000 directly would not be a fixture
    any real camera could have produced.
    """
    if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
        raise ValueError(f"({latitude}, {longitude}) is not a coordinate")

    def to_dms(
        value: float, positive: str, negative: str
    ) -> tuple[str, tuple[float, float, float]]:
        # Latitude is N/S and longitude is E/W. Writing "N" for a western longitude
        # silently yields a point on the far side of the planet — a fixture that
        # looks correct and measures 20,000 km from where it claims to be.
        hemisphere = positive if value >= 0 else negative
        magnitude = abs(value)
        degrees = int(magnitude)
        remainder = (magnitude - degrees) * 60
        minutes = int(remainder)
        seconds = round((remainder - minutes) * 60, 4)
        return hemisphere, (float(degrees), float(minutes), seconds)

    lat_ref, lat_dms = to_dms(latitude, "N", "S")
    lon_ref, lon_dms = to_dms(longitude, "E", "W")

    image = Image.fromarray(_gradient(width, height), mode="RGB")
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Canon"
    gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps_ifd[_GPS["GPSLatitudeRef"]] = lat_ref
    gps_ifd[_GPS["GPSLatitude"]] = lat_dms
    gps_ifd[_GPS["GPSLongitudeRef"]] = lon_ref
    gps_ifd[_GPS["GPSLongitude"]] = lon_dms
    exif[ExifTags.Base.GPSInfo] = gps_ifd
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, exif=exif)
    return buffer.getvalue()

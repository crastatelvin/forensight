"""Image metadata forensics: EXIF integrity indicators (P6.B3).

Metadata analysis is not a scored model. It is a set of discrete, individually
defensible indicators — *this file carries an editing application's name in its
Software field*, *the capture timestamp is missing* — each of which is a fact
about the file rather than an inference about its history.

The composite `score` is therefore the **fraction of indicators that fired**,
not a probability. That distinction is load-bearing: a report showing `0.6`
should not be read as "60% likely tampered", and the interpretation string plus
the per-indicator breakdown exist so it is not. Every indicator that fired is
named individually in the payload so an examiner can judge each one.

Absence of metadata is treated as a signal, not as cleanliness. An image
stripped of EXIF has lost provenance; that is worth surfacing, and silently
scoring it 0.0 would present a gap in the evidence as a reassuring result.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import ExifTags, Image
from PIL import __version__ as PILLOW_VERSION

from app.analysis.types import (
    AnalysisNotApplicable,
    ModuleSpec,
    Tier,
    build_result,
)

ALGORITHM_VERSION = "1.0.0"

#: EXIF metadata is not a scored model; it is indicator-counting.
TIER = Tier.B

DISCLAIMER = (
    "Metadata indicators describe what the file says about itself. Each "
    "indicator is a fact about the file, not proof of how it came to be. "
    "Metadata is trivially and invisibly editable."
)

#: Fraction of *scored* indicators that must fire before the file is flagged.
#: Two of the three, i.e. 0.667, clears this; a single indicator does not. One
#: missing field is common on genuinely original files — plenty of cameras and
#: every messaging app drop some of these — so a lone indicator is a prompt to
#: look, not a finding.
THRESHOLD = 0.4

#: EXIF tag numbers, resolved through Pillow's enum so the names stay readable.
_TAG = {
    "Make": ExifTags.Base.Make,
    "Model": ExifTags.Base.Model,
    "Software": ExifTags.Base.Software,
    "DateTime": ExifTags.Base.DateTime,
    "DateTimeOriginal": ExifTags.Base.DateTimeOriginal,
    "Artist": ExifTags.Base.Artist,
    "Copyright": ExifTags.Base.Copyright,
}


def _versions() -> str:
    return f"pil{PILLOW_VERSION}"


VERSION = f"{ALGORITHM_VERSION}+exif+{_versions()}"


def read_exif(data: bytes) -> dict[str, Any]:
    """Decoded EXIF as `{tag name: value}`, plus GPS presence.

    Returns an empty mapping for an image carrying no EXIF at all — that is a
    fact about the file, and the caller decides whether it matters.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        exif = image.getexif()
    except Exception as exc:  # noqa: BLE001 - undecodable input is a refusal
        raise AnalysisNotApplicable(
            f"metadata cannot be read: the evidence did not decode as an image ({exc})"
        ) from exc

    fields: dict[str, Any] = {}
    for name, tag in _TAG.items():
        value = exif.get(tag)
        if value not in (None, b"", ""):
            fields[name] = _stringify(value)

    # GPS lives in a sub-IFD. Only added when actually populated: injecting an
    # empty placeholder here would make a metadata-free image look non-empty and
    # silently skip the `no_exif` indicator.
    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except Exception:  # noqa: BLE001 - a corrupt GPS IFD must not fail the read
        gps = {}
    if gps:
        fields["GPS"] = dict(gps)
    return fields


def _stringify(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00 ")
    return str(value)


def indicators(fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate each indicator. Returns only those that fired, with a reason.

    Ordered by how strongly each bears on provenance, so the payload leads with
    the informative ones rather than alphabetically.
    """
    found: list[dict[str, Any]] = []
    if not fields:
        # With no metadata there is nothing left to check, so this is the whole
        # assessment rather than the first of several.
        return [
            {
                "id": "no_exif",
                "fired": True,
                "reason": "the image carries no EXIF metadata at all, so its "
                "provenance is unrecorded",
            }
        ]

    if "DateTimeOriginal" not in fields:
        found.append(
            {
                "id": "no_datetime_original",
                "fired": True,
                "reason": "capture timestamp (DateTimeOriginal) is absent",
            }
        )

    if "Make" not in fields and "Model" not in fields:
        found.append(
            {
                "id": "no_camera_identification",
                "fired": True,
                "reason": "neither camera Make nor Model is recorded",
            }
        )

    # DateTime present without DateTimeOriginal means the file was touched by
    # something that only rewrote the modification date.
    if "DateTime" in fields and "DateTimeOriginal" not in fields:
        found.append(
            {
                "id": "datetime_inconsistent",
                "fired": True,
                "reason": "DateTime is present but DateTimeOriginal is not: the "
                "file was re-saved by something that rewrote the modification "
                "date only",
            }
        )

    return found


def notable_fields(fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Facts that matter to an examiner but are not evidence of manipulation.

    Deliberately kept out of the score. Embedded GPS is a privacy exposure, not a
    sign of editing, and an untouched camera photo routinely carries both GPS and
    a Software field holding its own firmware string. Scoring either would mean
    flagging clean evidence, and treating a Software value as an editor's name
    would mean guessing which applications count. They are reported in full,
    because a case that turns on where a photo was taken needs them, without the
    module deciding that their presence makes a file "dirty".
    """
    notes: list[dict[str, Any]] = []
    if fields.get("GPS"):
        notes.append(
            {
                "id": "gps_present",
                "observation": "geolocation metadata is embedded in the file",
                "note": "location data present and to be handled as sensitive; "
                "its presence does not indicate manipulation",
            }
        )
    if fields.get("Software"):
        notes.append(
            {
                "id": "software_present",
                "observation": f"Software field names: {fields['Software']!r}",
                "note": "not scored: the same field holds camera firmware "
                "strings, so treating a value here as evidence of editing would "
                "be a guess about which applications are editors",
            }
        )
    return notes


#: Scored indicators, i.e. the denominator of the score. Pinned so that adding
#: one is a deliberate act which changes what past scores meant and shows up in a
#: diff, rather than a silent redefinition of a number already in the database.
#:
#: Deliberately short. Two more candidate indicators were written and removed: a
#: missing Artist/Copyright fires on entirely ordinary camera photos, and scoring
#: the Software field would mean guessing which applications are editors. Adding
#: indicators that fire on clean evidence makes the score worse, not better.
INDICATOR_COUNT = 3


def run(data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    fields = read_exif(data)
    fired = indicators(fields)
    # "no_exif" short-circuits: with no metadata there is nothing left to check.
    denominator = 1 if not fields else INDICATOR_COUNT
    score = len(fired) / denominator

    spec = ModuleSpec(
        name="image-exif",
        version=VERSION,
        tier=TIER,
        threshold=THRESHOLD,
        accepts=("image/jpeg",),
        disclaimer=DISCLAIMER,
    )
    detail: dict[str, Any] = {
        "score_is": "fraction of metadata indicators that fired, NOT a " "probability of tampering",
        "indicators_fired": fired,
        "indicators_fired_count": len(fired),
        "indicators_evaluated": denominator,
        "notable_fields": notable_fields(fields),
        "exif_present": bool(fields),
        "recorded_fields": sorted(k for k in fields if k != "GPS"),
        "gps_present": bool(fields.get("GPS")),
        "software": fields.get("Software"),
        "params": params,
        "known_limitations": [
            "EXIF is editable without altering the visible image, so a complete "
            "and internally consistent EXIF block proves nothing about origin",
            "absence of an indicator is not evidence of an unedited file",
            "many consumer devices write no EXIF at all, so missing camera "
            "fields are common on genuinely original files",
        ],
        "interpretation": (
            "metadata indicators present: " + "; ".join(item["reason"] for item in fired)
            if fired
            else "no metadata indicators fired"
        ),
    }
    return build_result(spec, score=score, detail=detail)


spec = ModuleSpec(
    name="image-exif",
    version=VERSION,
    tier=TIER,
    threshold=THRESHOLD,
    accepts=("image/jpeg",),
    params={},
    requires_gpu=False,
    disclaimer=DISCLAIMER,
)


class _ExifModule:
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
        # This module is a pure algorithm: no weights, so model_bytes is unused.
        return run(data, params)


module = _ExifModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

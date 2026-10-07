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


#: `ExifTags.GPSTAGS` is tag-id -> name; the readers below think in names.
_GPS_TAG_IDS: dict[str, int] = {name: tag for tag, name in ExifTags.GPSTAGS.items()}


def _raw_dms(gps: dict[Any, Any], tag_name: str) -> list[str]:
    """A DMS triple as strings, for display and for hand-checking."""
    raw = gps.get(_GPS_TAG_IDS.get(tag_name))
    if not isinstance(raw, tuple | list):
        return []
    out: list[str] = []
    for component in raw:
        try:
            out.append(f"{float(component):g}")
        except (TypeError, ValueError):
            out.append(str(component))
    return out


def gps_to_decimal(gps: dict[Any, Any]) -> dict[str, Any]:
    """The GPS IFD as signed decimal degrees, or `{}` when it cannot be read.

    Pillow hands back the raw IFD: ``GPSLatitude`` is a ``(degrees, minutes,
    seconds)`` triple and the hemisphere lives in a separate ``*Ref`` tag. Nothing
    downstream could use that — a map, a distance, a nearest-neighbour search all
    want signed decimal degrees — so it is converted here rather than in each
    consumer.

    Deliberately not scored and deliberately lossy about precision: EXIF stores
    rationals, and the third component is often absent, so the honest answer
    carries the values it actually had rather than a fabricated precision. Missing
    or malformed components yield `{}` rather than a guess at 0,0 — a point off the
    coast of Africa is worse than no point, because it looks like data.

    Returns the *raw* strings too. A coordinate in a forensic record has to be
    reproducible from the file, and "51°30'00"N" is what is in the bytes.
    """

    def _dms(tag_name: str) -> float | None:
        raw = gps.get(_GPS_TAG_IDS.get(tag_name))
        if not isinstance(raw, tuple | list) or not raw:
            return None
        parts: list[float] = []
        for component in raw[:3]:
            try:
                parts.append(float(component))
            except (TypeError, ValueError):
                # A missing seconds component is normal; a non-numeric one is not.
                if len(parts) >= 2:
                    parts.append(0.0)
                    break
                return None
        degrees = sum(part / (60.0**i) for i, part in enumerate(parts))
        return degrees if -180.0 <= degrees <= 180.0 else None

    lat = _dms("GPSLatitude")
    lon = _dms("GPSLongitude")
    if lat is None or lon is None:
        return {}

    # The ref carries the hemisphere: "S" and "W" mean negative.
    lat_ref = str(gps.get(_GPS_TAG_IDS["GPSLatitudeRef"], "N")).strip().upper()[:1]
    lon_ref = str(gps.get(_GPS_TAG_IDS["GPSLongitudeRef"], "E")).strip().upper()[:1]
    if lat_ref == "S":
        lat = -lat
    if lon_ref == "W":
        lon = -lon

    return {
        "latitude": round(lat, 7),
        "longitude": round(lon, 7),
        "latitude_ref": lat_ref,
        "longitude_ref": lon_ref,
        # The raw triples as text. `IFDRational` is not JSON-serialisable, and the
        # string form is the more useful record anyway: "51/1, 30/1, 0/1" is
        # exactly what the file holds, so an examiner can re-derive the decimal
        # value by hand and check this one.
        "raw_latitude": _raw_dms(gps, "GPSLatitude"),
        "raw_longitude": _raw_dms(gps, "GPSLongitude"),
        "note": "as recorded in the file's EXIF; not corroborated against any "
        "external source, and an EXIF coordinate is trivially editable",
    }


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
        # Decimal degrees, alongside the raw IFD triples. Reported, never scored:
        # an EXIF coordinate is editable without touching a pixel, so treating its
        # presence as evidence of anything would be the same mistake as scoring the
        # Software field. It is here because a case that turns on where a photo was
        # taken needs it.
        "gps": gps_to_decimal(fields.get("GPS", {})),
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

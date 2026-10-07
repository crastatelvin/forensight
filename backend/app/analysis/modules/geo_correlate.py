"""Geospatial correlation: where was this taken, relative to where?

Reads the GPS an image carries and measures it against a set of places the caller
names. The useful question in an investigation is rarely "was this image edited"
— it is "were these captures taken in the same place", and more pointedly "could
both of these have been taken here, given the time".

**No Shapely, no PostGIS.** The plan named both. What this actually needs is a
distance between two points on a sphere, which is haversine over `math` — and the
alternative would have added a compiled geometry dependency to the image for one
function. PostGIS earns its place when there are *areas* to test against (is this
point inside this jurisdiction's boundary), and nothing here asks that question
yet. Add it when something does.

**This is not a manipulation signal.** A photograph's GPS is a number in a file that
anyone can write, and phones reset it, inherit a stale value, or record a network
fix rather than a position. So the score is a distance, the threshold is a
co-location radius, and the detail says plainly what a match does not establish.
Reporting this as "these were taken in the same place" without that caveat would be
the exact failure this project exists to avoid — a confident number about a thing the
number cannot support.

**A correlation needs two things.** With GPS but nothing to compare against, or a
comparison target but no GPS on the evidence, the technique is undefined rather than
negative, so it refuses (`AnalysisNotApplicable`) instead of scoring zero. A 0.0
here would render as "nowhere near any of these places", which is a claim about the
world that nobody measured.

The threshold is a screening value measured on the fixtures in
`tests/fixtures_images.py`, not an operating point calibrated against any real
capture pattern. That is stated in the payload, every time.
"""

from __future__ import annotations

import math
from typing import Any

from app.analysis.types import AnalysisNotApplicable, ModuleSpec, Tier, build_result

ALGORITHM_VERSION = "1.0.0"

TIER = Tier.B

#: Co-location radius in kilometres. Two captures within this are reported as
#: co-located; the module says what that does and does not mean.
#:
#: Chosen as a screening value, not a measured one. There is no published operating
#: point for "these photographs are of the same place", because GPS error, EXIF
#: rounding and deliberate alteration all push in different directions by different
#: amounts depending on the device. A deployment that needs a defensible number here
#: has to calibrate it against its own capture population; see `calibration` in the
#: payload, which says so rather than implying this value came from anywhere.
THRESHOLD = 1.0

#: Below this, two points are the same point expressed twice. Reporting a distance
#: of 0.4 m as a measured separation would be spurious precision from a value EXIF
#: cannot carry in the first place.
FLOOR_KM = 0.01

DISCLAIMER = (
    "Geolocation of a photograph, from metadata the file itself carries. This is NOT "
    "proof that a camera was present at a place: GPS can be reset, inherited from a "
    "previous fix, recorded from a network rather than a satellite, or written "
    "arbitrarily by anyone editing the file. A close match means the coordinates are "
    "close, and nothing more. The radius is a screening value measured on synthetic "
    "test fixtures, not an operating point calibrated for any real capture pattern."
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "GPS metadata is self-reported: it can be reset, stale, network-derived, or "
    "written deliberately, and this module cannot distinguish those cases",
    "the radius is a screening value from synthetic fixtures, not calibrated against "
    "any real capture population",
    "a close match establishes that two coordinates agree, not that either camera "
    "was where its metadata says",
    "no timestamp is compared: whether two captures could both have been taken at "
    "one place depends on when they were taken, which this module does not read",
    "an image with no GPS refuses rather than scoring, so absence of coordinates is "
    "never rendered as a location",
)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres.

    Haversine rather than the law of cosines because the latter loses precision for
    short distances — which is exactly the regime this module operates in, where the
    threshold is 1 km and the interesting answers are metres.

    Mean Earth radius (6371.0088 km, IUGG). Spherical, so it is accurate to about
    0.3% against the WGS-84 ellipsoid; that is far below the error in the input
    coordinates and so is not the limiting factor here.
    """
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _coordinates(data: bytes) -> tuple[float, float] | None:
    """The evidence's own GPS, or None.

    Delegates to the EXIF reader rather than reimplementing the DMS conversion: a
    second hand-written GPS parser would be a second thing to get subtly wrong, and
    that one already declines to invent precision it cannot find.
    """
    from app.analysis.modules.image_exif import gps_to_decimal, read_exif

    # `read_exif` returns the raw GPS IFD keyed by tag *number*; `gps_to_decimal` is
    # what turns it into signed decimal degrees. Reading the raw dict directly is
    # what the first version of this did, and it silently returned no coordinates —
    # the module then reported every image as having no GPS, which reads as a
    # correct refusal rather than as a bug.
    gps = gps_to_decimal(read_exif(data).get("GPS") or {})
    latitude, longitude = gps.get("latitude"), gps.get("longitude")
    if latitude is None or longitude is None:
        return None
    return float(latitude), float(longitude)


def _references(params: dict[str, Any]) -> list[dict[str, Any]]:
    """The places to compare against, validated.

    `compare_to` is a list of `{label, latitude, longitude}`. It arrives as data
    rather than as evidence ids on purpose: a module may not read the object store,
    so the caller resolves ids to coordinates and hands over numbers. That keeps the
    module pure and means a caller cannot use it to reach another case's evidence.
    """
    raw = params.get("compare_to")
    if raw is None or raw == []:
        raise AnalysisNotApplicable(
            "no comparison targets were supplied: geospatial correlation needs this "
            "capture's coordinates plus at least one place to measure them against, "
            'set params.compare_to to [{"label": ..., "latitude": ..., "longitude": ...}]'
        )
    if not isinstance(raw, list):
        # Distinct from the empty case. "Nothing was supplied" and "what you supplied
        # is the wrong shape" need different fixes, and collapsing them sends the
        # caller looking for a missing parameter they already sent.
        raise AnalysisNotApplicable(
            f"compare_to must be a list of places, not {type(raw).__name__}"
        )

    out: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise AnalysisNotApplicable(
                f"compare_to[{index}] is not an object with latitude and longitude"
            )
        try:
            latitude = float(entry["latitude"])
            longitude = float(entry["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AnalysisNotApplicable(
                f"compare_to[{index}] has no usable latitude/longitude"
            ) from exc
        if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
            # Out of range is a malformed input, not a far-away place. Accepting it
            # would produce a distance in the thousands of kilometres that reads as a
            # measurement.
            raise AnalysisNotApplicable(
                f"compare_to[{index}] is not a valid coordinate " f"({latitude}, {longitude})"
            )
        out.append(
            {
                "label": str(entry.get("label") or f"reference-{index}"),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    return out


def run(data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    """Distance from this capture's GPS to the nearest named place."""
    here = _coordinates(data)
    if here is None:
        raise AnalysisNotApplicable(
            "this evidence carries no GPS coordinates, so there is no location to "
            "correlate; scoring it would report a distance to nowhere"
        )
    targets = _references(params)
    latitude, longitude = here

    distances = [
        {
            "label": target["label"],
            "latitude": target["latitude"],
            "longitude": target["longitude"],
            "distance_km": round(
                max(
                    haversine_km(latitude, longitude, target["latitude"], target["longitude"]),
                    FLOOR_KM,
                ),
                6,
            ),
        }
        for target in targets
    ]
    distances.sort(key=lambda item: item["distance_km"])
    nearest = distances[0]
    colocated = [item for item in distances if item["distance_km"] <= THRESHOLD]

    spec = ModuleSpec(
        name="geo-correlate",
        version=VERSION,
        tier=TIER,
        threshold=THRESHOLD,
        accepts=("image/jpeg", "image/png", "image/tiff"),
        disclaimer=DISCLAIMER,
    )

    detail: dict[str, Any] = {
        "score_is": (
            "great-circle distance in kilometres from this image's recorded GPS to "
            "the nearest supplied place; lower is closer"
        ),
        "origin": {"latitude": latitude, "longitude": longitude},
        "origin_source": "GPS metadata inside the image file, as recorded by the device",
        "distances_km": distances,
        "references_compared": len(distances),
        "nearest": nearest,
        "co_located_within_threshold": [item["label"] for item in colocated],
        "colocated_count": len(colocated),
        "radius_km": THRESHOLD,
        "calibration": {
            "threshold_source": "screening value measured on synthetic test fixtures",
            "calibrated_on_this_deployment": False,
            "note": (
                "not measured against any real capture population. A defensible "
                "operating point needs this deployment's own images with known "
                "origins, and the GPS error of the devices in use."
            ),
        },
        "known_limitations": list(KNOWN_LIMITATIONS),
        "params": params,
        "interpretation": (
            f"within {THRESHOLD} km of {len(colocated)} of {len(distances)} supplied "
            f"place(s); nearest is {nearest['label']} at {nearest['distance_km']} km. "
            "This compares coordinates, not presence."
        ),
    }
    return build_result(spec, score=nearest["distance_km"], detail=detail)


def _versions() -> str:
    from PIL import __version__ as PILLOW_VERSION

    return f"{ALGORITHM_VERSION}+exif+pil{PILLOW_VERSION}"


VERSION = _versions()

spec = ModuleSpec(
    name="geo-correlate",
    version=VERSION,
    tier=TIER,
    threshold=THRESHOLD,
    accepts=("image/jpeg", "image/png", "image/tiff"),
    params={
        "compare_to": {
            "type": "array",
            "items": {"label": "string", "latitude": "number", "longitude": "number"},
            "description": "Places to measure this capture against. Required.",
        }
    },
    disclaimer=DISCLAIMER,
)


class _GeoCorrelateModule:
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
        # Pure algorithm: no weights, and no second image.
        _ = model_bytes, aux_models, reference_bytes
        return run(data, params)


module = _GeoCorrelateModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

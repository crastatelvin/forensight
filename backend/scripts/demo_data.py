"""Populate a realistic forensic case so the README screenshots show the product.

**Not a fixture and not a test.** This exists to make `docs/screenshots/` show a case
that looks like an investigation rather than a row of `title: "t"` left behind by a test
run. It is idempotent-ish: it creates one case per run and is safe to repeat.

It goes through the **public API**, not the database, so what it produces is reachable
exactly the way a user's would be — including the audit trail. A seed script that writes
rows directly would produce a demo that differs from the product in precisely the ways a
screenshot hides.

Everything it creates is synthetic: fictional case numbers, invented names, no real
person, no real evidence, and images are the repository's own public-domain portraits.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = os.environ.get("FORENSIGHT_API", "http://localhost:8000")
EMAIL = os.environ.get("FORENSIGHT_EMAIL", "smoke@example.com")
#: No default, on purpose. A committed default password in a public repository is a
#: credential that will be found and tried, and a demo script is exactly where people look
#: for one. Create the account with `app.seed` first, then pass the password you chose.
PASSWORD = os.environ.get("FORENSIGHT_PASSWORD", "")
#: Where the public-domain fixture images live. Overridable because the demo script is
#: also run from a copy in /tmp inside the API container, where the repository layout does
#: not exist -- `parents[1]` off `__file__` then resolves to `/`, which is how the first
#: run silently uploaded nothing and reported success.
ASSETS = Path(
    os.environ.get("FORENSIGHT_ASSETS") or Path(__file__).resolve().parents[1] / "tests" / "assets"
)


def call(
    method: str,
    path: str,
    *,
    token: str | None = None,
    data: dict | None = None,
    raw: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, object]:
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    # noqa on the URL open below: API is a local, configured base, not user input.
    req = urllib.request.Request(f"{API}{path}", data=body, method=method)  # noqa: S310
    req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:  # noqa: S310 - fixed local host
            payload = response.read()
            try:
                return response.status, json.loads(payload)
            except json.JSONDecodeError:
                return response.status, payload
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:300].decode(errors="replace")


def login() -> str:
    if not PASSWORD:
        raise SystemExit(
            "set FORENSIGHT_PASSWORD to the password you created for "
            f"{EMAIL} with `python -m app.seed`"
        )
    status, payload = call("POST", "/auth/login", data={"email": EMAIL, "password": PASSWORD})
    if status != 200:
        raise SystemExit(f"login failed ({status}): {payload}")
    return payload["access_token"]  # type: ignore[index,return-value]


CASES = [
    {
        "number": "2026/FS/0417",
        "title": "Vehicle fire — origin determination, Barnet (ref 2026/0417)",
        "description": (
            "Passenger vehicle recovered from a residential street following a reported "
            "fire at 02:14. Origin is disputed between the engine bay and the front "
            "passenger footwell. Two witness statements conflict on the time the vehicle "
            "was last driven. Photographs and a scene sketch are attached."
        ),
        "priority": "high",
        "evidence": [
            ("turing_a.jpg", "Scene photograph — recovered vehicle, front left quarter"),
            ("bus.jpg", "Comparative scene photograph, same collection batch"),
        ],
    },
    {
        "number": "2026/FS/0392",
        "title": "Document examination — suspected forged maintenance log (ref 2026/0392)",
        "description": (
            "Maintenance log submitted as evidence in a fraud matter. Two pages appear "
            "to share identical ink characteristics across a six-month gap. Baseline "
            "handwriting comparison included."
        ),
        "priority": "normal",
        "evidence": [
            ("handwriting/p100.png", "Exhibited page — purport maintenance log entry"),
            ("handwriting/p160.png", "Comparative page, same document, adjacent entry"),
        ],
    },
]


#: Real coordinates, chosen so the map shows a cluster plus an outlier -- which is what
#: `geo-correlate` exists to notice. Fictional scene, real places.
#:   London (vehicle fire, Barnet) and the Metropolitan Police building, plus one
#:   deliberate outlier ~340 km away, so the panel has something worth plotting.
GPS = {
    "turing_a.jpg": [(51.6520, -0.1990), (51.6574, -0.2018)],  # Barnet, London
    "bus.jpg": [(51.5237, -0.1585), (51.5094, -0.1176)],  # Westminster to Barbican
}


def with_gps(data: bytes, filename: str) -> bytes:
    """Return the image with a GPS IFD written in, if it has coordinates.

    **This has to be a real GPS IFD with rational DMS triples**, because that is what
    `image_exif` reads -- it decodes `GPSLatitude` as a (degrees, minutes, seconds)
    rational triple plus a `GPSLatitudeRef`. The first version of this wrote plain string
    tags at top-level 0x8825/0x8826, which is not that structure at all; the analyzer
    returned no GPS, the panel said "No coordinates found", and the demo looked like the
    feature was broken when the feature was working and the fixture was wrong.

    The panel reads `detail.gps` from the **analyzer result**, not from EXIF directly, so
    the coordinates have to survive the analyzer for the map to show anything.
    """
    points = GPS.get(filename)
    if not points:
        return data
    try:
        from PIL import Image
        from PIL.ExifTags import IFD
        from PIL.TiffImagePlugin import IFDRational

        def dms(value: float) -> tuple[Any, Any, Any]:
            """Decimal degrees as the DMS rational triple EXIF stores."""
            magnitude = abs(value)
            degrees = int(magnitude)
            minutes_f = (magnitude - degrees) * 60
            minutes = int(minutes_f)
            seconds = (minutes_f - minutes) * 60
            return (
                IFDRational(degrees, 1),
                IFDRational(minutes, 1),
                IFDRational(int(round(seconds * 10000)), 10000),
            )

        image = Image.open(io.BytesIO(data))
        exif = image.getexif()
        # Keys of a GPS IFD are EXIF **tag ids**, not array offsets -- and they are not in
        # the order they read: 1=GPSLatitudeRef, 2=GPSLatitude, 3=GPSLongitudeRef,
        # 4=GPSLongitude. Writing 3/4 as the first coordinate pair stored the longitude in
        # the latitude slot and the analyzer read nothing.
        gps_ifd: dict[int, Any] = {
            1: "N" if points[0][0] >= 0 else "S",
            2: dms(points[0][0]),
            3: "E" if points[0][1] >= 0 else "W",
            4: dms(points[0][1]),
        }
        exif[IFD.GPSInfo] = gps_ifd
        out = io.BytesIO()
        image.save(out, "JPEG", quality=95, exif=exif)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - a missing GPS tag is not worth failing a demo over
        return data


def main() -> int:
    token = login()
    print(f"  signed in as {EMAIL}")

    # The case number is unique (`ix_cases_number`), which is correct behaviour -- a
    # repeated run must not collide with its own earlier output. Suffixed with a short
    # nonce rather than skipped, so re-running always leaves a usable demo behind.
    nonce = os.urandom(2).hex().upper()
    for spec in CASES:
        uploaded: list[str] = []
        status, case = call(
            "POST",
            "/cases",
            token=token,
            data={
                "number": f"{spec['number']}-{nonce}",
                "title": spec["title"],
                "description": spec["description"],
                "priority": spec["priority"],
            },
        )
        if status not in (200, 201):
            print(f"  case create failed ({status}): {case}")
            return 1
        case_id = case["id"]  # type: ignore[index]
        number = case.get("number", "?")  # type: ignore[union-attr]
        print(f"  case {number}: {spec['title'][:56]}")

        for filename, note in spec["evidence"]:
            source = ASSETS / filename
            if not source.is_file():
                print(f"    skip {filename} (looked in {source})")
                continue
            # multipart/form-data by hand: the API takes an upload and a note together,
            # and this keeps the script dependency-free.
            boundary = "----forensightdemo"
            buf = io.BytesIO()
            for field, value in (("note", note), ("filename", filename)):
                buf.write(f"--{boundary}\r\n".encode())
                buf.write(f'Content-Disposition: form-data; name="{field}"\r\n\r\n'.encode())
                buf.write(f"{value}\r\n".encode())
            buf.write(f"--{boundary}\r\n".encode())
            buf.write(
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
            )
            buf.write(b"Content-Type: image/jpeg\r\n\r\n")
            # Through `with_gps`, not the raw bytes: this is what gives the geospatial
            # panel something to plot. Reading the file directly here looked correct and
            # uploaded GPS-free evidence every run.
            buf.write(with_gps(source.read_bytes(), os.path.basename(filename)))
            buf.write(b"\r\n")
            buf.write(f"--{boundary}--\r\n".encode())

            status, payload = call(
                "POST",
                f"/cases/{case_id}/evidence",
                token=token,
                raw=buf.getvalue(),
                content_type=f"multipart/form-data; boundary={boundary}",
            )
            ok = status in (200, 201)
            print(f"    evidence {filename}: {'ok' if ok else status}")
            if ok and isinstance(payload, dict) and payload.get("id"):
                uploaded.append(payload["id"])
            elif not ok:
                print(f"      {payload}")

        # Queue analyses so the UI shows real analyzer output rather than an empty state.
        #
        # `/cases/{case_id}/analyses` needs an **evidence_id** -- an analysis runs against
        # one evidence item. The first version of this script posted to `/analyses`, which
        # does not exist, and then polled the same wrong path, so it reported a clean run
        # having queued and completed nothing. Both the route and the required field are
        # now read from the live OpenAPI schema instead of being recalled.
        for item in uploaded:
            for module in ("byte-entropy", "image-ela", "image-exif"):
                status, payload = call(
                    "POST",
                    f"/cases/{case_id}/analyses",
                    token=token,
                    data={
                        "evidence_id": item,
                        "module": module,
                    },
                )
                if status in (200, 201, 202):
                    print(f"    queued {module} on {item[:8]}")
                else:
                    print(f"    queue {module} FAILED ({status}): {str(payload)[:110]}")

    print("\n  waiting for the worker to drain the queue…")
    deadline = time.time() + 240
    while time.time() < deadline:
        status, cases = call("GET", "/cases", token=token)
        pending = 0
        total = 0
        if status == 200 and isinstance(cases, list):
            for entry in cases:
                if not str(entry.get("number", "")).startswith("2026/FS/"):
                    continue
                s2, jobs = call("GET", f"/cases/{entry['id']}/analyses", token=token)
                if s2 != 200 or not isinstance(jobs, list):
                    continue
                total += len(jobs)
                pending += sum(
                    1 for j in jobs if j.get("status") in ("queued", "running", "pending")
                )
            print(f"    {total} jobs, {pending} still pending")
            if total and not pending:
                break
        time.sleep(6)
    return 0


if __name__ == "__main__":
    sys.exit(main())

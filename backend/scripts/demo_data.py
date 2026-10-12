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


#: Two cases, both synthetic, both labelled for what the files actually are.
#:
#: The evidence notes used to describe the repository's portrait fixtures as "recovered
#: vehicle, front left quarter" under a vehicle-fire narrative. A portrait is not a
#: vehicle, and a demo that mislabels its own evidence is exactly the habit this platform
#: exists to discourage -- so the case now describes photographs, which is what they are.
CASES = [
    {
        "number": "2026/FS/0417",
        "title": "Image authenticity review — photographs of uncertain provenance (ref 2026/0417)",
        "description": (
            "Two photographs received from an anonymous tipster, used in a publication "
            "the subject disputes. The publisher asserts they were taken at the same "
            "location on the same day. The subject states they are unrelated images. "
            "Original files as received are attached unaltered."
        ),
        "priority": "high",
        "evidence": [
            ("turing_a.jpg", "Photograph under examination — as received, unaltered"),
            ("bus.jpg", "Photograph under examination — as received, unaltered"),
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

#: A question the evidence genuinely cannot answer. Error levels, entropy and EXIF do not
#: identify a photographer, so the agent should decline rather than guess -- which is the
#: behaviour worth demonstrating, and it is now true for an honest reason instead of
#: because the case was built on a fire the evidence never mentioned.
AGENT_OBJECTIVE = "State who took these two photographs, and the date each was captured."

#: Real coordinates, chosen so the map has two distinct clusters to plot. Fictional case,
#: real places: Barnet and central London.
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
    case_ids: list[str] = []
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
        case_ids.append(case_id)
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

    if os.environ.get("FORENSIGHT_DEMO_AGENT") == "1":
        run_agent(token, case_ids)
    return 0


def run_agent(token: str, case_ids: list[str]) -> None:
    """Start one agent run and drive it to completion, so the console has content.

    Opt-in behind `FORENSIGHT_DEMO_AGENT=1` because it needs a reachable LLM and takes
    minutes on a CPU: `qwen2.5:3b` generates at roughly 5 tokens/second here, and the
    objective is deliberately one the evidence cannot answer.

    **`POST /runs` alone does not run the agent.** It creates the row and returns; the
    model work happens on the SSE stream. Posting and then polling for status leaves the
    run at `pending` forever, which reads as a stalled system and is in fact the designed
    behaviour. So the stream is opened, and that is what advances the graph.
    """
    if not case_ids:
        return
    case_id = case_ids[0]
    status, run = call(
        "POST", f"/agent/cases/{case_id}/runs", token=token, data={"objective": AGENT_OBJECTIVE}
    )
    if status not in (200, 201) or not isinstance(run, dict):
        print(f"  agent run failed to start ({status}): {run}")
        return
    run_id = run["run_id"]
    print(f"\n  agent run {run_id[:8]} on {AGENT_OBJECTIVE}")

    # Streamed as raw text: this only needs to *drive* the run, not parse it.
    # noqa on the URL: API is a local, configured base, not user input.
    request = urllib.request.Request(  # noqa: S310
        f"{API}/agent/runs/{run_id}/stream", method="GET"
    )
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=1800) as stream:  # noqa: S310
            for raw in stream:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                status_now = str(event.get("status", ""))
                if "refusal" in event:
                    print(f"    {status_now}: {event['refusal'][:150]}")
                if status_now in {"completed", "failed", "declined", "awaiting_approval"}:
                    break
    except Exception as exc:  # noqa: BLE001 - a demo must not traceback at the summary
        print(f"  agent stream ended: {exc}")


if __name__ == "__main__":
    sys.exit(main())

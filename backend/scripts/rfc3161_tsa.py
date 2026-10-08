"""An RFC 3161 timestamp authority for labs that run their own PKI.

A self-hosted lab cannot reach a public TSA — that is rather the point of being
air-gapped — and a PAdES baseline-B signature stops establishing anything the moment the
signing certificate expires. This service closes that gap: it answers RFC 3161 timestamp
requests over HTTP so `REPORT_TIMESTAMP_URL` has something to point at, and the reports
this deployment signs then carry a countersignature plus their own validation material.

**The cryptography is OpenSSL's, not this file's.** Requests arrive as DER, go to
`openssl ts -reply`, and the signed response comes back. Nothing here parses ASN.1, builds
a `TimeStampResp`, or touches a signing key — which is the point: the part of a timestamp
authority that is easy to get subtly wrong is exactly the part a lab should not be
reimplementing to save a dependency.

**A TSA is only as good as its certificate, and this deployment's certificate is
self-signed.** That is the correct trade for a lab whose verifiers are its own staff, and
it is worthless as evidence to anyone else. Two consequences, both real:

- The timestamp proves the signature existed at a stated time *against the lab's own
  key*. A verifier who does not trust that key gains nothing from it — which is why
  `verify_pdf` reports `timestamped` separately from `trusted` rather than collapsing them.
- Nothing here is a *trusted* timestamp authority in the ETSI sense. A report that must
  be relied on outside the lab needs a certificate from one.

Run it from the image:

    docker compose exec api python scripts/rfc3161_tsa.py --port 8080

or standalone:

    python scripts/rfc3161_tsa.py --port 8080 --signer tsa.crt --key tsa.key --serial tsa.ser

`scripts/provision_tsa.py` creates a usable certificate, serial file and config.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger("rfc3161_tsa")

#: RFC 3161 responses are DER, and a request is small. Anything larger is not a timestamp
#: query — pyHanko hashes the signature and sends a few hundred bytes — so the cap exists
#: to stop this endpoint being used as a general-purpose upload.
MAX_REQUEST_BYTES = 64 * 1024

#: Seconds for one `openssl ts -reply`. Generous, because the first call after a restart
#: loads a key; short enough that a hung authority does not hold a signing request open.
OPENSSL_TIMEOUT = 30


class Rejected(Exception):
    """OpenSSL declined the request. Carries the reason to return to the client."""


def openssl() -> str:
    """The `openssl` binary, resolved once.

    Resolved rather than assumed: a bare `"openssl"` is a partial path, which resolves
    against whatever `PATH` the service happens to have, and its absence would otherwise
    surface as a `FileNotFoundError` from inside a subprocess call rather than as the
    plain statement of what is missing.
    """
    found = shutil.which("openssl")
    if found is None:
        raise Rejected("no openssl binary is on PATH; this authority cannot issue timestamps")
    return found


def timestamp(config: Path, request_der: bytes) -> bytes:
    """One signed `TimeStampResp`, from `openssl ts -reply`.

    **Query and response go through files, not stdin.** `openssl ts -reply -queryfile` is
    the documented interface and it keeps a two-byte framing detail out of this file.
    """
    with tempfile.TemporaryDirectory() as workdir:
        query = Path(workdir) / "request.tsq"
        response = Path(workdir) / "response.tsr"
        query.write_bytes(request_der)
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, nothing interpolated
                [
                    openssl(),
                    "ts",
                    "-reply",
                    "-queryfile",
                    str(query),
                    "-config",
                    str(config),
                    "-out",
                    str(response),
                ],
                capture_output=True,
                timeout=OPENSSL_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise Rejected(f"the timestamp authority took longer than {OPENSSL_TIMEOUT}s") from exc

        # Everything that touches the response file stays inside this block. An earlier
        # version checked for it and read it *after* the temporary directory was removed,
        # so every successful reply was reported as a rejection — with OpenSSL's
        # "Response has been generated." as the reason, which is about as misleading as a
        # failure message can be.
        if not response.is_file():
            # OpenSSL's stderr names the reason ("Error during serial number generation"),
            # and it is about this service's own configuration rather than the caller's, so
            # it is safe and useful to return. Only the last line is passed through:
            # OpenSSL writes the banner and the useful part last.
            reason = done.stderr.decode(errors="replace").strip().splitlines()
            raise Rejected(reason[-1] if reason else f"openssl exited {done.returncode}")

        body = response.read_bytes()

        # A granted response can still be a *rejected* one: OpenSSL answers a query it
        # cannot satisfy with a well-formed TimeStampResp carrying status Rejected, and
        # returning that as HTTP 200 would push the failure onto the client as a
        # cryptographic error rather than as the service's own refusal. The exit code is
        # zero either way, so the status has to be read out of the response.
        #
        # Checked as raw bytes rather than by parsing ASN.1, which keeps this file free of
        # the part that is easy to get subtly wrong — and a false negative is harmless,
        # because pyHanko rejects an unauthorised response on its own.
        if b"Rejected" in body and b"Granted" not in body:
            raise Rejected("the authority rejected the query; see its log for the reason")

        return body


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        logger.info("%s %s", self.command, self.path)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        # pyHanko's HTTPTimeStamper only POSTs, but a bare GET returning 405 rather than a
        # 404 saves someone an hour of wondering whether the service is up.
        self._send(405, b'{"error":"POST a DER-encoded TimeStampReq"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self.path.rstrip("/").endswith("/timestamp"):
            self._send(404, b'{"error":"no such endpoint"}', "application/json")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, b'{"error":"Content-Length was not a number"}', "application/json")
            return
        if length <= 0:
            self._send(400, b'{"error":"empty request"}', "application/json")
            return
        if length > MAX_REQUEST_BYTES:
            self._send(
                413,
                b'{"error":"request too large for a timestamp query"}',
                "application/json",
            )
            return

        try:
            der = timestamp(self.server.config, self.rfile.read(length))  # type: ignore[attr-defined]
        except Rejected as exc:
            logger.warning("rejected a timestamp request: %s", exc)
            self._send(503, json_error(str(exc)), "application/json")
            return
        except Exception as exc:  # noqa: BLE001 - never let one request kill the service
            logger.exception("timestamp request failed unexpectedly")
            self._send(500, json_error(f"internal error: {type(exc).__name__}"), "application/json")
            return

        self._send(200, der, "application/timestamp-reply")


def json_error(message: str) -> bytes:
    """A JSON error body, escaped.

    `json.dumps` rather than string formatting, because the message can contain an
    OpenSSL error string with quotes in it — and an unescaped quote turns a readable
    refusal into a parse error on the caller's side, which is where the actual problem
    gets lost.
    """
    import json

    return json.dumps({"error": message}).encode()


def main() -> None:
    parser = argparse.ArgumentParser(description="An RFC 3161 timestamp authority (OpenSSL-backed)")
    parser.add_argument("--port", type=int, default=8080)
    # Loopback by default; a container must be passed `--host 0.0.0.0` to be reachable,
    # and taking that exception at the call site is better than defaulting a signing
    # service onto every interface.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--config",
        default="/etc/forensight/tsa/openssl-tsa.cnf",
        help="OpenSSL ts configuration naming the signer certificate, key and serial file",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    config = Path(args.config)
    if not config.is_file():
        raise SystemExit(
            f"no OpenSSL ts configuration at {config}. Run scripts/provision_tsa.py, or "
            "pass --config."
        )

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.config = config  # type: ignore[attr-defined]
    logger.info("RFC 3161 timestamp authority listening on %s:%s", args.host, args.port)
    logger.warning(
        "this authority's certificate is this deployment's own; timestamps it issues "
        "prove the time against that key and nothing more"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

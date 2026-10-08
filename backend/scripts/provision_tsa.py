#!/usr/bin/env python
"""Create the key material for a self-hosted RFC 3161 timestamp authority.

Run once per deployment. Produces three files that `scripts/rfc3161_tsa.py` serves
from and that `REPORT_TIMESTAMP_URL` points at:

* a self-signed certificate with the `timeStamping` extended key usage,
* its private key, and
* a serial file plus an OpenSSL `ts` configuration.

**Self-signed is the intended case, not a shortcut.** A lab runs its own PKI and is its
own root of trust. What that buys is real and is what long-term validation needs: the
countersignature is made by a key the lab controls, so the asserted time cannot be
back-dated by a third party and does not depend on reaching anything outside the
deployment — which is the constraint that made a public TSA unusable in the first place.

**What it does not buy** is a timestamp anyone outside the lab should rely on. Trust in a
timestamp is trust in the TSA's certificate, and this certificate is one the lab issued to
itself. `verify_pdf` therefore reports `timestamped` separately from `trusted`: a report
carrying one of these timestamps says the signature existed at the stated time, and says
nothing to a reader who does not already trust this key.

The certificate is deliberately *not* a CA and is deliberately short-lived relative to a
signing certificate's typical life: a TSA whose own certificate outlives the reports it
timestamps buys nothing extra, since the timestamp's value is bounded by the trust the
verifier places in it at verification time.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

#: OpenSSL's `ts` wants a starting serial. `01` is the lowest legal value; the file is a
#: counter OpenSSL increments per issued timestamp, and an empty one is the single most
#: common reason a fresh TSA answers every request with "Error during serial number
#: generation".
INITIAL_SERIAL = "01"

#: 10 years. Long enough that reports signed now can still be timestamped when a signing
#: certificate rotates, short enough that a compromise retires it.
DEFAULT_DAYS = 3650


def generate(out_dir: Path, days: int, subject_cn: str) -> dict[str, Path]:
    """The certificate, key, serial file and OpenSSL config."""
    out_dir.mkdir(parents=True, exist_ok=True)
    key_path = out_dir / "tsa.key"
    cert_path = out_dir / "tsa.crt"
    serial_path = out_dir / "tsa.ser"
    config_path = out_dir / "openssl-tsa.cnf"

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    # `critical=True` on EKU: an RFC 3161 certificate is only usable for timestamping, and
    # a non-critical extension lets the same key be used for anything else, which is how a
    # TSA key quietly ends up being a general signing key.
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(key_path, 0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    serial_path.write_text(INITIAL_SERIAL)
    os.chmod(serial_path, 0o600)

    config_path.write_text(
        "[ tsa ]\n"
        "default_tsa = tsa_config1\n"
        "\n"
        "[ tsa_config1 ]\n"
        f"signer_cert = {cert_path}\n"
        f"signer_key = {key_path}\n"
        f"certs = {cert_path}\n"
        f"serial = {serial_path}\n"
        "crypto_device = builtin\n"
        "signer_digest = sha256\n"
        "default_policy = 1.3.6.1.4.1.99999.1.1\n"
        "other_policies = 1.3.6.1.4.1.99999.1.2\n"
        "digests = sha256, sha384, sha512\n"
        "accuracy = secs:1, millisecs:500, microsecs:100\n"
        "clock_precision_digits = 0\n"
        "ordering = yes\n"
        "tsa_name = yes\n"
        "ess_cert_id_chain = no\n"
        "ess_cert_id_alg = sha256\n"
    )
    return {
        "key": key_path,
        "cert": cert_path,
        "serial": serial_path,
        "config": config_path,
    }


def smoke_test(config: Path) -> bool:
    """Ask the new authority for a timestamp and check it says Granted.

    **Run before the service is considered working.** An OpenSSL `ts` configuration that
    looks right and answers every request with an error is the common outcome, and finding
    that out here rather than during the first report signature is the whole value of this
    step.
    """
    binary = shutil.which("openssl")
    if binary is None:
        print("no openssl binary is on PATH; this authority cannot be provisioned", file=sys.stderr)
        return False

    with tempfile.TemporaryDirectory() as workdir:
        query = Path(workdir) / "q.tsq"
        response = Path(workdir) / "r.tsr"
        done = subprocess.run(  # noqa: S603 - fixed argv, nothing interpolated
            [binary, "ts", "-query", "-data", "/dev/null", "-sha256", "-out", str(query)],
            capture_output=True,
            check=False,
        )
        if done.returncode != 0:
            print(f"could not build a timestamp query: {done.stderr.decode(errors='replace')}")
            return False
        done = subprocess.run(  # noqa: S603
            [
                binary,
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
            check=False,
        )
        if done.returncode != 0:
            print(f"the authority refused its own query: {done.stderr.decode(errors='replace')}")
            return False
        done = subprocess.run(  # noqa: S603
            [binary, "ts", "-reply", "-in", str(response), "-text"],
            capture_output=True,
            check=False,
        )
        return b"Status: Granted" in done.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="/etc/forensight/tsa")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--subject", default="ForenSight Lab Timestamp Authority")
    args = parser.parse_args()

    paths = generate(Path(args.out_dir), args.days, args.subject)

    print("Created:")
    for name, path in paths.items():
        print(f"  {name:7} {path}")

    print()
    if not smoke_test(paths["config"]):
        print("The authority did not issue a timestamp for its own request.", file=sys.stderr)
        print("Do not point REPORT_TIMESTAMP_URL at it until this passes.", file=sys.stderr)
        return 1

    print("Self-test passed: the authority issued a timestamp for its own request.")
    print()
    print("Next:")
    print(f"  1. run the service:   python scripts/rfc3161_tsa.py --config {paths['config']}")
    print("  2. point the API at it: REPORT_TIMESTAMP_URL=http://<host>:8080/timestamp")
    print(f"  3. trust it when verifying: supply {paths['cert']} to POST /reports/verify")
    print()
    print("This certificate is self-signed. Timestamps it issues prove the time against")
    print("this key and nothing more; they are not evidence to anyone who does not")
    print("already trust it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

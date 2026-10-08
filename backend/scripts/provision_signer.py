#!/usr/bin/env python
"""Create a self-signed signing key for case reports.

Run once per deployment. The output is a PKCS#12 file holding an EC P-256 key and
a certificate, which `REPORT_SIGNING_P12` points at.

Self-signed is the intended case, not a shortcut. A forensic lab signs its own
reports and is its own root of trust; nobody is going to buy a certificate from a
public authority for "the guy who runs the analysis server". What self-signing
*cannot* give you is identity for a reader who has not decided to trust your
certificate, which is why the report names the signer and prints the certificate's
SHA-256 fingerprint on its face, and why `verify_pdf` reports `trusted: false`
until a trust root is supplied. The integrity claim — the bytes have not changed
since signing — holds either way.

The key is written with a passphrase. A passphrase is required by the signature
format rather than for security theatre: the certificate and key travel together in
one file, and an unencrypted PKCS#12 on disk is a signing key anyone can read can
use to sign reports that look like yours.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID, ObjectIdentifier

#: id-kp-nonRepudiation. Absent from `cryptography`'s `ExtendedKeyUsageOID`, and named
#: here rather than inlined at the one use site so the certificate's key usage is
#: identifiable in a `openssl x509 -text` diff.
NON_REPUDIATION = ObjectIdentifier("1.3.6.1.5.5.7.3.2")


def generate(
    out_path: Path,
    passphrase: str,
    *,
    common_name: str,
    organisation: str,
    days: int,
) -> x509.Certificate:
    """Write a fresh key and certificate. Refuses to overwrite silently."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        raise SystemExit(
            f"{out_path} already exists. Delete it first if you really mean to "
            f"replace the signing key: existing reports were signed with it, and "
            f"they will no longer verify against the new certificate."
        )

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organisation),
        ]
    )
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
            # Key usage is scoped to document signing. A signing certificate that
            # could also sign code or authenticate to a server is a certificate
            # that can be misused for things nobody chose.
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
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
        .add_extension(
            # id-kp-nonRepudiation (1.3.6.1.5.5.7.3.2), written out because
            # `cryptography` has no constant for it. This used to say `emailProtection`,
            # which is simply the wrong key usage for signing a report — and it went
            # unnoticed because baseline-B signing never validated the certificate's
            # chain. Turning on long-term validation did, and refused to sign:
            # RFC 5280 path validation checks the extended key usage, so a certificate
            # that may only protect email is not a valid signer for a document. A lab's
            # report-signing certificate now says what it is for.
            x509.ExtendedKeyUsage([NON_REPUDIATION]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    blob = pkcs12.serialize_key_and_certificates(
        name=common_name.encode(),
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase.encode()),
    )
    # 0600: a signing key readable by another local user is a signing key another
    # local user can use.
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(blob)
    return cert


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=os.environ.get("REPORT_SIGNING_P12", "signing/report-signer.p12"),
        help="where to write the PKCS#12 file (default: $REPORT_SIGNING_P12)",
    )
    parser.add_argument("--common-name", default="ForenSight case report signer")
    parser.add_argument("--organisation", default="ForenSight")
    parser.add_argument("--days", type=int, default=825, help="certificate lifetime")
    parser.add_argument(
        "--passphrase",
        default=None,
        help="key passphrase; prompted for when omitted",
    )
    args = parser.parse_args()

    passphrase = args.passphrase or getpass.getpass("New signing key passphrase: ")
    if len(passphrase) < 8:
        raise SystemExit("a signing key passphrase of at least 8 characters")

    out = Path(args.out)
    cert = generate(
        out,
        passphrase,
        common_name=args.common_name,
        organisation=args.organisation,
        days=args.days,
    )

    print(f"wrote {out} ({out.stat().st_size} bytes, mode 0600)")
    print(f"  subject:     {cert.subject.rfc4514_string()}")
    print(f"  valid until: {cert.not_valid_after_utc.isoformat()}")
    print()
    print("Set in the api environment:")
    print(f"  REPORT_SIGNING_P12={out}")
    print("  REPORT_SIGNING_PASSPHRASE=<the passphrase>")
    print()
    print(
        "Keep the passphrase out of version control and out of the compose file. "
        "Distribute the certificate (not the key) to whoever needs to verify "
        "reports; they pass it as a trust root and then `trusted` becomes true."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

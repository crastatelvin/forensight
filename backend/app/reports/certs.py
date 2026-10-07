"""Loading certificates from the byte forms a lab might actually have.

Small on purpose. It exists so `signing.py` and the report builder do not each grow
their own PEM/DER/PKCS#12 branch, and so there is exactly one place that knows a
`.pem` and a `.p12` are the same thing expressed differently.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12


def load_certificates(data: bytes, passphrase: str | None = None) -> list[x509.Certificate]:
    """Certificates from PEM, DER, or a PKCS#12 archive — whichever the bytes are.

    Trust material arrives in all three forms depending on who issued it, and
    refusing two of them would mean the lab has to convert its certificate before
    it can verify its own reports. The sniffing is by content, not by file
    extension, because the extension is often the thing that is wrong.
    """
    if not data:
        return []

    # PKCS#12 is a binary container with a distinctive ASN.1 prefix.
    if data[:1] == b"\x30" and b"encrypted" not in data[:64]:
        try:
            _key, cert, extra = pkcs12.load_key_and_certificates(
                data, passphrase.encode() if passphrase else None
            )
        except ValueError:
            # A PKCS#12 with no passphrase supplied fails here, and falling through
            # to the PEM/DER reader reports it as "these bytes are not a
            # certificate" — which is true and useless. Someone handing over their
            # trust bundle as a .p12 deserves to be told the passphrase is the
            # missing part.
            if passphrase is None:
                raise ValueError(
                    "this looks like a PKCS#12 archive, which needs its passphrase "
                    "to be read. Supply the passphrase, or pass the bare "
                    "certificate instead."
                ) from None
            return _from_pem_or_der(data)
        return [c for c in (cert, *extra) if c is not None]

    return _from_pem_or_der(data)


def _from_pem_or_der(data: bytes) -> list[x509.Certificate]:
    try:
        return x509.load_pem_x509_certificates(data)
    except ValueError:
        pass
    try:
        return [x509.load_der_x509_certificate(data)]
    except ValueError as exc:
        raise ValueError(
            "no certificate could be read from these bytes; expected PEM, DER or "
            "a PKCS#12 archive"
        ) from exc


def first_certificate_der(data: bytes, passphrase: str | None = None) -> bytes:
    """One certificate's DER bytes — what a fingerprint is computed over."""
    certs = load_certificates(data, passphrase)
    if not certs:
        raise ValueError("no certificate found in the supplied bytes")
    from cryptography.hazmat.primitives.serialization import Encoding

    return certs[0].public_bytes(Encoding.DER)


def subject_line(cert: x509.Certificate) -> str:
    """A certificate subject as one readable line.

    `NameAttribute.value` is `str | bytes` because some X.520 attributes are byte
    strings. Printing one raw puts a `b'...'` repr where a name belongs, so a
    non-text attribute is shown as hex rather than dropped — a reader should be able
    to see that an attribute exists even when it is not a name.
    """
    parts: list[str] = []
    for attr in cert.subject:
        value = attr.value
        shown = value if isinstance(value, str) else value.hex()
        parts.append(f"{attr.rfc4514_attribute_name}={shown}")
    return ", ".join(parts)


def subject_of(data: bytes, passphrase: str | None = None) -> str:
    """A certificate's subject as one readable line."""
    certs = load_certificates(data, passphrase)
    if not certs:
        return "unknown signer"
    return subject_line(certs[0])

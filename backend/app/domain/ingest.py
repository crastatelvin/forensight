"""Evidence ingest validation: filename sanitization, MIME sniffing, size.

Kills path-traversal (T2.2) by never trusting the client filename for storage
and by content-addressing objects by SHA-256. MIME is sniffed from bytes, not
from the client-supplied content-type (T2.3).
"""
from __future__ import annotations

import hashlib
import os
import re

import magic

# Conservative allow-list of evidence MIME types.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/tiff",
        "application/pdf",
        "text/plain",
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "video/mp4",
        "application/zip",
    }
)

MAX_EVIDENCE_BYTES = 200 * 1024 * 1024  # 200 MiB

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class EvidenceValidationError(ValueError):
    """Raised when uploaded evidence fails validation."""


def secure_filename(filename: str) -> str:
    """Return a safe basename. Strips directories and dangerous chars (T2.2)."""
    # Take basename only — discards ../ and absolute path components.
    base = os.path.basename(filename.replace("\\", "/"))
    base = base.strip().lstrip(".") or "unnamed"
    base = _SAFE_NAME_RE.sub("_", base)
    return base[:255]


def sniff_mime(data: bytes) -> str:
    return magic.from_buffer(data[:4096], mime=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_upload(*, filename: str, data: bytes) -> tuple[str, str, str]:
    """Validate an upload. Returns (safe_name, mime, sha256).

    Raises EvidenceValidationError on oversize or disallowed type (T2.3).
    """
    if len(data) == 0:
        raise EvidenceValidationError("Empty file")
    if len(data) > MAX_EVIDENCE_BYTES:
        raise EvidenceValidationError(
            f"File exceeds maximum size of {MAX_EVIDENCE_BYTES} bytes"
        )
    mime = sniff_mime(data)
    if mime not in ALLOWED_MIME_TYPES:
        raise EvidenceValidationError(f"Disallowed content type: {mime}")
    return secure_filename(filename), mime, sha256_hex(data)

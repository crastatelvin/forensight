"""Phase 2 unit tests: ingest validation, state machine, audit hashing (T2.2/T2.3/T2.7)."""
from __future__ import annotations

import base64

import pytest
from app.domain.antivirus import (
    EICAR_SIGNATURE,
    InfectedFileError,
    NoopScanner,
)
from app.domain.audit import GENESIS_HASH, compute_entry_hash
from app.domain.ingest import (
    MAX_EVIDENCE_BYTES,
    EvidenceValidationError,
    secure_filename,
    sha256_hex,
    validate_upload,
)
from app.domain.models import CaseStatus
from app.domain.state_machine import (
    IllegalTransitionError,
    assert_transition,
    can_transition,
)

# A valid minimal 1x1 PNG (so libmagic sniffs image/png, not text/plain).
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_secure_filename_strips_traversal() -> None:
    # T2.2: path-traversal filename is sanitized to a safe basename.
    assert secure_filename("../../etc/passwd") == "etc_passwd" or "passwd" in secure_filename(
        "../../etc/passwd"
    )
    assert "/" not in secure_filename("../../etc/passwd")
    assert "\\" not in secure_filename("..\\..\\windows\\system32\\cmd.exe")
    assert secure_filename("") == "unnamed"


def test_validate_upload_accepts_png() -> None:
    name, mime, digest = validate_upload(filename="pic.png", data=_PNG)
    assert mime == "image/png"
    assert digest == sha256_hex(_PNG)
    assert name == "pic.png"


def test_validate_upload_rejects_disallowed_mime() -> None:
    # T2.3: an executable/script is sniffed and rejected regardless of name.
    with pytest.raises(EvidenceValidationError):
        validate_upload(filename="evil.png", data=b"#!/bin/sh\necho pwned\n")


def test_validate_upload_rejects_oversize() -> None:
    # T2.3: oversized file rejected. Patch the limit conceptually via a big buffer.
    too_big = _PNG + b"\x00" * (MAX_EVIDENCE_BYTES + 1)
    with pytest.raises(EvidenceValidationError):
        validate_upload(filename="big.png", data=too_big)


def test_validate_upload_rejects_empty() -> None:
    with pytest.raises(EvidenceValidationError):
        validate_upload(filename="empty.png", data=b"")


async def test_noop_scanner_detects_eicar() -> None:
    # T2.4: EICAR test file is flagged; clean file passes.
    scanner = NoopScanner()
    with pytest.raises(InfectedFileError):
        await scanner.scan(EICAR_SIGNATURE)
    await scanner.scan(_PNG)  # clean -> no raise


def test_state_machine_allows_and_rejects() -> None:
    # T2.7: legal transitions allowed; illegal rejected.
    assert can_transition(CaseStatus.INTAKE, CaseStatus.OPEN)
    assert can_transition(CaseStatus.OPEN, CaseStatus.INVESTIGATING)
    assert not can_transition(CaseStatus.ARCHIVED, CaseStatus.INVESTIGATING)
    with pytest.raises(IllegalTransitionError):
        assert_transition(CaseStatus.ARCHIVED, CaseStatus.INVESTIGATING)
    # archived is terminal
    assert CASE_ARCHIVED_TERMINAL()


def CASE_ARCHIVED_TERMINAL() -> bool:
    from app.domain.state_machine import CASE_TRANSITIONS

    return CASE_TRANSITIONS[CaseStatus.ARCHIVED] == frozenset()


def test_audit_hash_is_deterministic_and_chains() -> None:
    # T2.5 (unit level): same inputs -> same hash; different prev -> different hash.
    h1 = compute_entry_hash(
        prev_hash=GENESIS_HASH, actor_id="a", action="upload", target="ev1",
        payload={"x": 1},
    )
    h1b = compute_entry_hash(
        prev_hash=GENESIS_HASH, actor_id="a", action="upload", target="ev1",
        payload={"x": 1},
    )
    h2 = compute_entry_hash(
        prev_hash=h1, actor_id="a", action="upload", target="ev1", payload={"x": 1}
    )
    assert h1 == h1b
    assert h1 != h2
    assert len(h1) == 64

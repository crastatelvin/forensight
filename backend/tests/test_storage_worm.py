"""Live MinIO WORM verification (T2.6) — skipped unless FORENSIGHT_MINIO_LIVE=1.

Confirms the content-addressed WORM bucket rejects overwrite/delete of committed
evidence within retention. Requires a running MinIO (compose `core` profile).
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

pytestmark = pytest.mark.skipif(
    os.environ.get("FORENSIGHT_MINIO_LIVE") != "1",
    reason="requires live MinIO (set FORENSIGHT_MINIO_LIVE=1)",
)


def _store():
    from app.config import Settings
    from app.domain.storage import ObjectStore

    settings = Settings(
        secret_key="x" * 20,
        minio_endpoint="localhost:9000",
        minio_access_key="forensight",
        minio_secret_key="change-me-in-real-env",
        minio_secure=False,
    )
    return ObjectStore(settings)


def test_worm_rejects_overwrite_and_delete() -> None:
    store = _store()
    store.ensure_bucket()
    key = uuid.uuid4().hex + "0" * 32  # 64-char pseudo-hash key
    store.put_evidence(key=key, data=_PNG, content_type="image/png")

    # Re-put with same key + different bytes must NOT change the object (WORM).
    store.put_evidence(key=key, data=_PNG + b"tampered", content_type="image/png")
    assert store.get_evidence(key) == _PNG

    from minio import Minio
    from minio.deleteobjects import DeleteObject

    client: Minio = store._client  # noqa: SLF001 - test introspection

    # The committed version must carry a COMPLIANCE retention lock.
    obj = client.stat_object("evidence", key)
    retention = client.get_object_retention("evidence", key, version_id=obj.version_id)
    assert retention is not None and retention.mode == "COMPLIANCE"

    # Deleting the specific locked version within retention must fail.
    errors = list(
        client.remove_objects(
            "evidence",
            [DeleteObject(key, version_id=obj.version_id)],
        )
    )
    assert errors, "expected object-lock to reject deletion of the locked version"


def test_missing_object_is_a_file_not_found_not_an_s3_error() -> None:
    """An absent object must surface as `FileNotFoundError`.

    Every caller in this codebase already handles `FileNotFoundError` as "the
    evidence is not available". Letting the minio SDK's `S3Error` past that turned
    the condition into an unhandled 500 with a stack trace — in the download
    endpoint *and* in the in-app viewer.

    Found by running the real stack: a case held an evidence row whose object was
    never in MinIO, and viewing it produced a 500. The green suite could not see
    it, because nothing constructed that state.

    Matched on the error *code*, not the class: the SDK raises one `S3Error` for
    every failure, so a class check would miss `NoSuchKey` completely.
    """
    store = _store()
    store.ensure_bucket()
    missing = uuid.uuid4().hex + "f" * 32

    with pytest.raises(FileNotFoundError) as caught:
        store.get_evidence(missing)
    # The message has to name the object, or whoever is chasing a missing artifact
    # has nothing to search for.
    assert missing in str(caught.value)


def test_an_unprovisioned_bucket_is_reported_the_same_way() -> None:
    """`NoSuchBucket` is an operational fault, not a missing file.

    Both mean "the bytes are not retrievable", so both get the same exception type
    — but the distinction is in the *class check*, and if it ever regressed to
    `NoSuchKey` only, a deployment with no evidence bucket would return a raw S3
    error again. Asserted directly rather than by creating a bucket-less store.
    """
    from app.domain.storage import _is_missing

    class _S3ish(Exception):
        def __init__(self, code: str) -> None:
            super().__init__(code)
            self.code = code

    assert _is_missing(_S3ish("NoSuchKey")) is True
    assert _is_missing(_S3ish("NoSuchBucket")) is True
    assert _is_missing(_S3ish("AccessDenied")) is False
    # A code-less error must not be treated as "missing" -- that would disguise an
    # auth or network fault as an absent file.
    assert _is_missing(Exception("boom")) is False

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

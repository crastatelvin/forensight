"""Object storage (MinIO/S3) with a WORM bucket for evidence (T2.1, T2.6).

Evidence is content-addressed: the object key is the SHA-256 hex. The bucket is
created with object-lock enabled and objects are written with a retention lock,
so committed evidence cannot be overwritten or deleted within retention.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

from minio import Minio
from minio.commonconfig import COMPLIANCE
from minio.retention import Retention

from app.config import Settings

EVIDENCE_BUCKET = "evidence"
DEFAULT_RETENTION_DAYS = 3650  # 10 years


class ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key or None,
            secret_key=settings.minio_secret_key or None,
            secure=settings.minio_secure,
        )

    def ensure_bucket(self) -> None:
        # object_lock requires the bucket be created with it enabled.
        if not self._client.bucket_exists(EVIDENCE_BUCKET):
            self._client.make_bucket(EVIDENCE_BUCKET, object_lock=True)

    def object_exists(self, key: str) -> bool:
        try:
            self._client.stat_object(EVIDENCE_BUCKET, key)
            return True
        except Exception:  # noqa: BLE001 - stat raises if missing
            return False

    def put_evidence(self, *, key: str, data: bytes, content_type: str) -> str:
        """Idempotent content-addressed write with WORM retention (T2.1/T2.6)."""
        if self.object_exists(key):
            # Same content-hash -> identical object; dedupe (T2.1).
            return key
        retain_until = datetime.now(UTC) + timedelta(days=DEFAULT_RETENTION_DAYS)
        self._client.put_object(
            EVIDENCE_BUCKET,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
            retention=Retention(COMPLIANCE, retain_until),
        )
        return key

    def get_evidence(self, key: str) -> bytes:
        """Read stored evidence bytes.

        A missing object raises `FileNotFoundError`, not the raw S3 error. That is
        not a cosmetic translation: `NoSuchKey` means the database has a row whose
        bytes are gone — a real, serious condition — and every caller in this
        codebase already handles `FileNotFoundError` as "the evidence is not
        available". Letting the SDK exception past it turned that into an
        unhandled 500 with a stack trace, in the download endpoint and in the
        viewer, for a condition the code already knew how to name.

        The distinction from an *integrity* failure is preserved: a hash mismatch
        is raised by the caller, not here, because "the bytes are wrong" and "the
        bytes are absent" are different findings and must not be reported alike.
        """
        try:
            resp = self._client.get_object(EVIDENCE_BUCKET, key)
        except Exception as exc:  # noqa: BLE001 - translated, not swallowed
            if _is_missing(exc):
                raise FileNotFoundError(
                    f"evidence object {key!r} is not present in storage; the record "
                    f"exists but its bytes are gone"
                ) from exc
            raise
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()


def _is_missing(exc: BaseException) -> bool:
    """Whether a storage error means "this object is not there".

    Matched on the error *code* rather than the exception class, because the
    minio SDK raises one `S3Error` for every failure and only the code
    distinguishes them. Checking the class would miss `NoSuchKey` entirely.

    `NoSuchBucket` is included deliberately: an unprovisioned bucket is the same
    operational situation from the caller's point of view — the bytes are not
    retrievable — and reporting it as "the object is missing" sends whoever is
    fixing it to the wrong place. The message still names the bucket so the real
    cause is visible.
    """
    code = getattr(exc, "code", None)
    return code in {"NoSuchKey", "NoSuchBucket"}

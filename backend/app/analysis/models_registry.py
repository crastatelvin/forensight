"""Model registry on MinIO: pinned versions + checksum verify (P4.B5).

A model artifact is stored under `models/<module>/<version>/<file>` and is never
loaded until its SHA-256 matches the value pinned in the module's ModuleSpec. A
mismatch refuses the load (T4.3) rather than falling back — a model that is not
the one we pinned produces a score we cannot defend.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.analysis.types import ModelChecksumError
from app.config import Settings

MODEL_BUCKET = "models"


class ModelStore:
    """Fetch and verify model artifacts. Read-only by design."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    def _minio(self) -> Any:
        if self._client is None:
            from minio import Minio

            self._client = Minio(
                self._settings.minio_endpoint,
                access_key=self._settings.minio_access_key or None,
                secret_key=self._settings.minio_secret_key or None,
                secure=self._settings.minio_secure,
            )
        return self._client

    @staticmethod
    def object_key(*, module: str, version: str, filename: str) -> str:
        return f"{module}/{version}/{filename}"

    def put_model(
        self, *, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        """Store a model artifact. Used by provisioning, not by the API path."""
        import io

        client = self._minio()
        if not client.bucket_exists(MODEL_BUCKET):
            client.make_bucket(MODEL_BUCKET)
        client.put_object(
            MODEL_BUCKET,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return key

    def get_model(self, key: str) -> bytes:
        """Fetch one artifact.

        A missing bucket is reported as a `ModelChecksumError` rather than
        propagating an S3 error. The distinction matters: "the model store has not
        been provisioned" is an operator problem with a known fix, and surfacing it
        as a raw S3 exception puts an SDK string in front of the person who has to
        solve it. It also keeps the failure inside the executor's existing refusal
        path, so the job lands in `failed` with a sentence rather than a stack.
        """
        try:
            resp = self._minio().get_object(MODEL_BUCKET, key)
        except Exception as exc:  # noqa: BLE001 - every S3 failure is a refusal
            name = type(exc).__name__
            if "NoSuchBucket" in name or "NoSuchKey" in name:
                raise ModelChecksumError(
                    f"model store is not provisioned: {name} for "
                    f"{MODEL_BUCKET}/{key}. Run scripts/fetch_models.sh and upload "
                    f"the artifact with ModelStore.put_model."
                ) from exc
            raise ModelChecksumError(f"could not read model {key!r}: {exc}") from exc
        try:
            data: bytes = resp.read()
            return data
        finally:
            resp.close()
            resp.release_conn()

    def load_verified(self, *, key: str, expected_sha256: str) -> bytes:
        """Load a model only if it matches `expected_sha256`.

        The whole point: a model swapped or corrupted in the bucket must not
        silently produce a different score under the same version label.
        """
        data = self.get_model(key)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            raise ModelChecksumError(
                f"model {key!r} checksum mismatch: expected {expected_sha256}, "
                f"got {actual}; refusing to load"
            )
        return data

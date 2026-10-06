#!/usr/bin/env python3
"""Provision the MinIO model bucket from artifacts fetched locally.

Analyzers with pinned weights load their artifacts from the `models` bucket at
run time, verified against the SHA-256 in the module's `ModuleSpec`. Nothing
creates that bucket or uploads anything automatically, which is correct — it must
be a deliberate provisioning step — but it means a fresh deployment fails every
model-backed job with a bucket error until someone does this.

Run after `scripts/fetch_models.sh`:

    backend/scripts/fetch_models.sh
    python backend/scripts/provision_models.py

Idempotent: an artifact already present with the right digest is left alone, so
re-running after a model version bump uploads only what changed.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from app.analysis.models_registry import MODEL_BUCKET, ModelStore
from app.analysis.types import ModelChecksumError
from app.config import get_settings

#: (ModuleSpec.model_key, filename on disk). Kept next to the digests already
#: pinned in each module, so provisioning cannot drift from what the modules
#: expect: an artifact is uploaded only if its digest matches what the module will
#: verify against.
ARTIFACTS: tuple[tuple[str, str], ...] = (
    # object-detect
    ("object-detect/yolov4-416/yolov4.onnx", "yolov4.onnx"),
    # face-compare: the embedder, then the detector fetched under `#detector`
    ("face-compare/1.0.0/sface.onnx", "sface.onnx"),
    ("face-compare/1.0.0/sface.onnx#detector", "yunet.onnx"),
)


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".models")
    if not source.is_dir():
        print(
            f"ERROR: no artifacts in {source}. Run scripts/fetch_models.sh first.",
            file=sys.stderr,
        )
        return 2

    settings = get_settings()
    store = ModelStore(settings)
    uploaded = skipped = 0

    for key, filename in ARTIFACTS:
        path = source / filename
        if not path.exists():
            print(f"MISSING {filename} (expected {path})", file=sys.stderr)
            return 2

        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        print(f"{key}\n  {len(data) / 1e6:.1f} MB  sha256={digest[:16]}...")

        try:
            existing = store.get_model(key)
        except ModelChecksumError:
            existing = b""

        if hashlib.sha256(existing).hexdigest() == digest:
            print("  already present with matching digest; leaving it")
            skipped += 1
            continue

        store.put_model(key=key, data=data, content_type="application/octet-stream")
        print("  uploaded")
        uploaded += 1

    print(
        f"\nDone: {uploaded} uploaded, {skipped} already current. "
        f"Bucket: {MODEL_BUCKET} at {settings.minio_endpoint}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
# Fetch the pinned analysis model artifacts (P6).
#
# The weights are large and so are not committed: YOLOv4-tiny is 257 MB. Each is
# verified against the SHA-256 pinned in the module's ModuleSpec before it is
# placed, because the executor refuses to load a model whose digest does not
# match -- and a test that trusts an unverified download is not a test.
#
# Usage:  scripts/fetch_models.sh [dest_dir]     (default: .models)
# Then:   FORENSIGHT_YOLO_WEIGHTS=.models/yolov4.onnx pytest tests/test_object_detect.py
#
# In production the artifacts live in the MinIO `models` bucket under
# `models/<module>/<version>/<file>`, uploaded via `ModelStore.put_model`. This
# script is for a developer machine and for CI.
set -euo pipefail

DEST="${1:-.models}"
mkdir -p "$DEST"

fetch() {
  local name="$1" url="$2" sha="$3" out="$DEST/$1"

  if [[ -f "$out" ]]; then
    local have
    have="$(sha256sum "$out" | cut -d' ' -f1)"
    if [[ "$have" == "$sha" ]]; then
      echo "ok       $name (already present, digest matches)"
      return
    fi
    echo "stale    $name (digest mismatch, refetching)"
  fi

  echo "fetching $name ..."
  curl -fsSL --retry 3 -o "$out.part" "$url"

  local got
  got="$(sha256sum "$out.part" | cut -d' ' -f1)"
  if [[ "$got" != "$sha" ]]; then
    rm -f "$out.part"
    echo "FAILED   $name: expected $sha, got $got" >&2
    exit 1
  fi
  mv "$out.part" "$out"
  echo "ok       $name"
}

fetch "yolov4.onnx" \
  "https://media.githubusercontent.com/media/onnx/models/main/validated/vision/object_detection_segmentation/yolov4/model/yolov4.onnx" \
  "1881fe9c506c970d7866cb4bfc33bda791ce46951a3c39c45ace2ff2b9daf369"

echo
echo "Done. Weights verified by digest."
echo "To run the detection tests:"
echo "  FORENSIGHT_YOLO_WEIGHTS=$DEST/yolov4.onnx pytest tests/test_object_detect.py"
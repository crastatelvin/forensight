#!/usr/bin/env bash
# Fetch the pinned analysis model artifacts (P6).
#
# The weights are large and so are not committed: YOLOv4-tiny is 257 MB. Each is
# verified against the SHA-256 pinned in the module's ModuleSpec before it is
# placed, because the executor refuses to load a model whose digest does not
# match -- and a test that trusts an unverified download is not a test.
#
# Usage:  scripts/fetch_models.sh [dest_dir]     (default: .models)
# Then:   FORENSIGHT_YOLO_WEIGHTS=$DEST/yolov4.onnx pytest tests/test_object_detect.py
#         pytest tests/test_face_compare.py       (defaults to $DEST, no env needed)
#
# Total ~300 MB. YOLOv4 dominates; the two face models are 39 MB between them.
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

# Object detection (P6.B2): YOLOv4-tiny, official ONNX model zoo export.
fetch "yolov4.onnx" \
  "https://media.githubusercontent.com/media/onnx/models/main/validated/vision/object_detection_segmentation/yolov4/model/yolov4.onnx" \
  "1881fe9c506c970d7866cb4bfc33bda791ce46951a3c39c45ace2ff2b9daf369"

# Face comparison (P6.B1): YuNet detector + SFace embedder, both Apache-2.0 from
# the OpenCV model zoo. Apache-2.0 rather than InsightFace's ArcFace weights,
# which are released for non-commercial research only -- not a licence a forensic
# platform can ship under.
fetch "yunet.onnx" \
  "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" \
  "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

fetch "sface.onnx" \
  "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx" \
  "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"

echo
echo "Done. Weights verified by digest."
echo "To run the model-backed tests:"
echo "  FORENSIGHT_YOLO_WEIGHTS=$DEST/yolov4.onnx pytest tests/test_object_detect.py"
echo "  pytest tests/test_face_compare.py"
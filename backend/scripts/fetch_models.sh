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

# Speaker comparison (P9.B2): ECAPA-TDNN, Apache-2.0 from the Wespeaker project,
# trained on VoxCeleb2. Apache-2.0 rather than SpeechBrain's own release, whose
# weights carry no stated licence — and this project's fixture gate refuses
# unstated terms.
#
# NOTE: the model takes 80-bin log-mel features, not raw audio, so the front-end in
# `app/analysis/modules/speaker_compare.py` has to match the model's own config
# (25 ms frames, 10 ms hop, 80 mel bins). A wrong front-end does not fail — it returns
# embeddings from a pipeline the model was never trained on.
fetch "ecapa.onnx" \
  "https://huggingface.co/Wespeaker/wespeaker-voxceleb-ecapa-tdnn512/resolve/main/voxceleb_ECAPA512.onnx" \
  "f901ae3571912bad4e11e157744cb8b7d77a0b46923b83c410bdbb6c77d69736"

# AudioSet classifier, for P9.B4 gunfire triage. **Fetched and digest-pinned, but NOT wired
# to an analyzer** — the item stays unticked because only one gunfire recording exists under
# a licence this project accepts, so there is nothing to measure a threshold against. See
# `tests/probe_audio_fixtures.py` and CHECKLIST.md under P9.B4. Fetched anyway because the
# digest is the pin, and re-deriving it by hand is how a pin goes stale.
#
# MIT, 527 AudioSet classes, `Gunshot, gunfire` at index 427. Input is a (1, 1024, 128)
# log-mel spectrogram at 16 kHz — and getting that front end wrong does not fail, it returns
# a confident 527-way distribution that is confidently wrong. The 527 class names are
# committed at tests/assets/audio/ast_labels.json rather than fetched: 11 KB of metadata is
# not worth a digest, and committing them lets the probe run with no network at all.
fetch "ast-audioset.onnx" \
  "https://huggingface.co/Xenova/ast-finetuned-audioset-10-10-0.4593/resolve/main/onnx/model.onnx" \
  "6ff353b1e809da25f900b54494fd4ac12a6fc7db1e25195e238f21be125bb4bd"

# Deepfake detector for P9.B3. **Fetched and digest-pinned, but NOT wired to an analyzer** --
# see CHECKLIST.md under P9.B3 for why. Fetched anyway: the model is chosen and verified,
# the front end is written, and only permissively-licensed fixtures are missing.
#
# `buildborderless/CommunityForensics-DeepfakeDet-ViT`, MIT, ViT-Small/384, trained on 2.7M
# samples across 4,803 generators (Community Forensics, CVPR 2025). It is the model that
# WORKS here: 29/30 generated flagged against 0/13 genuine, where the previously-evaluated
# detector managed 8/8 and 3/3.
#
# Three things about it are easy to get wrong and are recorded so the next reader does not:
#   - `num_labels: 1`, so the output is a SIGMOID, not a softmax over two classes. Treating
#     the logit as a probability is wrong by construction.
#   - Its preprocessor is CLIP normalisation (mean 0.48145466/0.4578275/0.40821073), resize
#     shortest_edge 440, centre-crop 384. That is a THIRD set of constants in this script:
#     the deepfake-v2 model wants 0.5/0.5 and ordinary CNNs want ImageNet.
#   - The card explicitly forbids squashing to 384x384 -- it distorts facial geometry and
#     destroys accuracy.
# It loads on this project's onnxruntime 1.20.1 despite the card recommending >= 1.27.
fetch "communityforensics-vit.onnx" \
  "https://huggingface.co/buildborderless/CommunityForensics-DeepfakeDet-ViT/resolve/main/onnx/model.onnx" \
  "a42c7d740fbb345ba9a26d469b22f301d73089ce3c6da993877ed2b6965a8ba1"

echo
echo "NOTE: ast-audioset.onnx has no analyzer. Two of the six clips in"
echo "tests/assets/audio are not what their filenames claim, and there is one genuine"
echo "gunfire recording where a measurement needs several. Run the probe with an"
echo "interpreter that has transformers, torch and onnxruntime:"
echo "  python tests/probe_audio_fixtures.py"
echo
echo "NOTE: communityforensics-vit.onnx has no analyzer either, for a narrower reason:"
echo "it measures well but its honest fixture set would have to be real generated images,"
echo "and every reachable set is CC BY-NC-SA-4.0, which the fixture gate refuses."
echo
echo "Done. Weights verified by digest."
echo "To run the model-backed tests:"
echo "  FORENSIGHT_YOLO_WEIGHTS=$DEST/yolov4.onnx pytest tests/test_object_detect.py"
echo "  pytest tests/test_face_compare.py"
echo "  pytest tests/test_golden_speaker.py"
# Third-party notices

This file covers the **model weights and datasets** ForenSight fetches at runtime.
The source code is under the Apache License 2.0 in `LICENSE` — that licence applies
to the code only, and **not** to the weights.

The distinction matters and is easy to get wrong: a permissive licence on the code
does nothing whatsoever for a model file downloaded from someone else's server. Each
artifact below keeps its own terms, and a deployment is responsible for satisfying
them.

Nothing here is bundled in the repository. Every artifact is fetched by
`backend/scripts/fetch_models.sh` (weights) or `backend/scripts/fetch_fixtures.py`
(fixtures), both of which verify a pinned SHA-256 before writing anything. That is
what makes the table below checkable rather than aspirational: the digest in the
script is the digest of the file you get.

## Pinned model weights

| Module | Artifact | Upstream | Digest (SHA-256, truncated) | Terms |
|---|---|---|---|---|
| `object-detect` | `yolov4.onnx` | [ONNX Model Zoo](https://github.com/onnx/models) via `media.githubusercontent.com` | `1881fe9c506c970d…` | MIT (ONNX models repository) |
| `face-compare` (detector) | `face_detection_yunet_2023mar.onnx` | [OpenCV Zoo](https://github.com/opencv/opencv_zoo) | `8f2383e4dd3cfbb4…` | MIT (OpenCV Zoo) — trained on the WIDER FACE dataset |
| `face-compare` (embedder) | `face_recognition_sface_2021dec.onnx` | [OpenCV Zoo](https://github.com/opencv/opencv_zoo) | `0ba9fbfa01b5270c…` | MIT (OpenCV Zoo) — trained on VGGFace2 |

The digests above are duplicated in code at `backend/app/analysis/modules/*.py` and are
verified before the model is loaded, so a substituted artifact cannot be used silently.

### A note on the face models specifically

The SFace and YuNet weights are published in the OpenCV Zoo under MIT, and were
trained on VGGFace2 and WIDER FACE respectively. **Training-data terms are a separate
question from the weights' own licence**, and are worth checking before a commercial
deployment. ForenSight makes no claim that the bundled weights are free of
downstream restrictions beyond what their upstream states, and the
`face-compare` module's own disclaimer is explicit that it is not an identification
system.

## Not redistributed

ForenSight ships no weights, datasets or third-party binaries in the repository.
Everything is fetched at provisioning time and digest-verified.

## Fixtures

No forensic fixtures are pinned yet. `scripts/fetch_fixtures.py` exists and enforces a
licence allowlist — it refuses gated sources, sources with no stated licence, and
CC BY-ND and CC BY-SA — but the Phase 9 fixture sets are not currently obtainable in a
form that satisfies it. See `CHECKLIST.md` under P9.B1–B6 for the per-analyzer
research, including why each candidate was rejected.

## Reporting a licence problem

If an artifact here is mis-licensed, or a pinned digest no longer matches what its
upstream serves, that is a bug worth reporting. Both are checkable:

```bash
cd backend
.venv/bin/python -m scripts.fetch_fixtures --help    # the gate and its refusals
bash scripts/fetch_models.sh                          # weight pins and digests
```
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
| `speaker-compare` | `voxceleb_ECAPA512.onnx` | [Wespeaker](https://huggingface.co/Wespeaker/wespeaker-voxceleb-ecapa-tdnn512) | `f901ae3571912bad…` | Apache-2.0 (Wespeaker) — trained on VoxCeleb2 |

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

Six excerpts in `backend/tests/assets/authorship/`, cut deterministically from three
novels downloaded from [Project Gutenberg](https://www.gutenberg.org). They are in the
United States public domain, which places them outside copyright entirely — the
clearest licence position available and the reason these were chosen over every
labelled dataset researched for Phase 9.

| Author | Work | Gutenberg ID |
|---|---|---|
| Jane Austen | *Pride and Prejudice* | 1342 |
| Herman Melville | *Moby-Dick* | 1661 |
| Mary Shelley | *Frankenstein* | 84 |

The full texts are 450 KB–1.3 MB each and are not committed; the excerpts are, at
~6000 words each. `scripts/fetch_fixtures.py --gutenberg` re-derives them from a
digest-verified source and refuses to write bytes that do not match the pin, so a
changed derivation fails loudly instead of silently invalidating the pinned scores.

### Public-domain audio (`speaker-compare`)

Eight 8-second WAV excerpts in `backend/tests/assets/speaker/`, taken from
[LibriVox](https://librivox.org) recordings hosted on [archive.org](https://archive.org).
LibriVox contributions are dedicated to the **public domain** by the volunteers who
record them, and the books they read are themselves out of copyright.

Readers: Kara Shallenberg (*Letters of Two Brides*, *The Secret Garden*, *The
Odyssey*), Cynthia Lyons (*Bleak House*, *Beowulf*, *Mother Goose in Prose*) and
Michael Sirois (*Penguin Island*, *Narrative of Arthur Gordon Pym*). Full provenance,
including how to regenerate the excerpts, is in
`backend/tests/assets/speaker/PROVENANCE.md`.

**These do not demonstrate that speaker verification works.** Same-reader similarity
across them runs 0.328–0.911 while different-reader similarity reaches 0.841, so the
ranges overlap. The cause is the material rather than the technique: LibriVox readings
are *dramatic readings*, one narrator performing several characters, so
`kara_b` (*The Secret Garden*, read in character voices) sits further from its own
narrator than from a different reader. It is retained and asserted as a failure rather
than dropped.

### Public-domain prose (`text-authorship`)

Six excerpts in `backend/tests/assets/authorship/`, cut deterministically from three
novels downloaded from [Project Gutenberg](https://www.gutenberg.org). They are in the
United States public domain, which places them outside copyright entirely — the
clearest licence position available and the reason these were chosen over every
labelled dataset researched for Phase 9.

**These do not support the conclusion one might assume.** The six excerpts do not
separate by author — same-author similarity falls to 0.934 while a different-author
pair reaches 0.957. `tests/test_golden_authorship.py` asserts that overlap rather than
tuning a threshold until it disappears, and `CHECKLIST.md` under P9.B6 records why
fixing the fixture size at the point where it flatters the technique was rejected.

### Not pinned

The remaining Phase 9 fixture sets (fingerprint, deepfake, gunshot, handwriting)
are not obtainable in a form the allowlist accepts — every ungated
candidate states no licence, and the ones that do are gated, non-commercial, or No
Derivatives. `scripts/fetch_fixtures.py` enforces the allowlist and refuses all of
them. See `CHECKLIST.md` under P9.B1–B5 for the per-analyzer research, including why
each candidate was rejected.

## Reporting a licence problem

If an artifact here is mis-licensed, or a pinned digest no longer matches what its
upstream serves, that is a bug worth reporting. Both are checkable:

```bash
cd backend
.venv/bin/python -m scripts.fetch_fixtures --help    # the gate and its refusals
bash scripts/fetch_models.sh                          # weight pins and digests
```
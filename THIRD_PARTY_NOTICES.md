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

## Runtime services (containers, not redistributed)

Distinct from the weights and fixtures above: these are **container images pulled at deploy
time**, not artefacts this repository fetches, digest-pins and can therefore vouch for by
hash. They carry their own obligations, and one of them is not permissive.

| Service | Image | Licence | Status |
|---|---|---|---|
| Postgres 16 + PostGIS + pgvector | `pgvector/pgvector:pg16` + `postgresql-16-postgis-3` | PostgreSQL / PostGIS (OSI) | maintained |
| Redis | as pinned in `docker-compose.yml` | see upstream | maintained |
| **MinIO** | **`cgr.dev/chainguard/minio:latest`** | **AGPL-3.0** | **archived upstream** |

### MinIO: AGPL-3.0, and archived

**This is the one dependency in the stack with a copyleft licence and no upstream
maintenance, and neither fact is recorded anywhere else in this repository.** Measured
2026-10-09 against the running container:

```
$ docker exec forensight-minio-1 minio --version
License: GNU AGPLv3 - https://www.gnu.org/licenses/agpl-3.0.html
$ docker inspect forensight-minio-1 --format '{{.Config.Image}}'
cgr.dev/chainguard/minio:latest
```

The running release identifies itself as `RELEASE.2026-09-22T19-25-18Z`, and MinIO's own
distribution now answers **HTTP 410 Gone** for every artefact, with this body:

> The open-source MinIO Server, MinIO Client (mc) and MinIO KES projects are archived and
> no longer maintained. MinIO does not provide product support, security updates, or
> security advisories for them, and does not accept or process vulnerability reports
> concerning them.

**Why the AGPL matters here, stated plainly.** AGPL-3.0 is a network-copyleft licence:
section 13 extends copyleft to users interacting with a modified version over a network.
ForenSight does not modify MinIO — it runs the image unmodified and speaks S3 to it — so
the obligation that bites in practice is **section 6, conveying**: distributing a
container that runs AGPL MinIO conveys the licence, and a recipient must be told and given
the corresponding source. A deployment that ships this image as part of a product bundle is
distributing it. **A deployment that wants to avoid that obligation cannot**, short of
replacing the object store, because it is the image, not how ForenSight uses it.

**Why "archived" matters more than the licence for a forensic tool.** Evidence in this
system is COMPLIANCE-locked and content-addressed, so its durability and its retention
guarantees are load-bearing claims. An object store that ships no security advisories cannot
be told to an examiner as "receives security updates". That is a statement about the
evidence store's assurance, and it belongs in the threat model rather than only here.

**This is a known, recorded gap, not a discovered surprise** — but it was recorded
nowhere, which is the problem this entry fixes. See CHECKLIST.md under P10.B2 for the
replication consequence, which is the same root cause: **the tooling needed to configure
MinIO replication is no longer distributed at all.**

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

### Cryptography libraries (not redistributed, no weights)

Two libraries do the cryptographic work and both are pulled in by the dependency
lock, not committed:

- **pyHanko** (MIT) — PAdES signing, verification and the incremental-difference
  analysis that decides whether a signature still covers the document. Also the source
  of the RFC 3161 client used for long-term validation. No licence issue; recorded here
  because it is the component every integrity claim in a report rests on.
- **OpenSSL** (Apache-2.0) — used at *provisioning and run time* for the
  self-hosted RFC 3161 timestamp authority (`scripts/rfc3161_tsa.py` forwards DER to
  `openssl ts -reply`). **Not a Python dependency and not in the image**: it must be on
  `PATH` on the host or inside the container for the TSA to run, and the TSA refuses
  with a plain statement if it is not. The cryptography is OpenSSL's rather than a
  reimplementation here, because ASN.1 timestamp responses are exactly the thing not to
  hand-roll.

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

### Public-domain manuscript (`handwriting-match`)

Four greyscale page images in `backend/tests/assets/handwriting/`, from *Charlotte
Baker's 1914 diary* (item `csdhi_000081`, archive.org), digitised by the Center for
Digital History at UNH. Licence: **CC Public Domain Mark 1.0**.

The 1914 volume interleaves diary entries with printed ephemera, and ink density does
not separate them — a density filter would have put printed tide tables into a
handwriting fixture set. Pages were located on a 288-page contact sheet and each
verified at full resolution. Full provenance and regeneration steps are in
`backend/tests/assets/handwriting/PROVENANCE.md`.

**These support document matching only.** All four pages are one writer, so the writer
confound is untested.

### Bundled basemap (`handwriting`-style, geospatial panel)

`frontend/public/basemap/ne-50m-land.json` — *Natural Earth* `ne_50m_land` (1:50m
scale), from [`nvkelso/natural-earth-vector`](https://github.com/nvkelso/natural-earth-vector).

**Public domain.** Per <https://www.naturalearthdata.com/about/terms-of-use/>:

> All versions of Natural Earth raster + vector map data found on this website are in the
> public domain. You may use the maps in any manner, including modifying the content and
> design, electronic dissemination, and offset printing.

Authors: Tom Patterson, Nathaniel Vaughn Kelso, and contributors. 1.6 MB, digest-pinned
(`sha256 e874b27a…`), excluded from `prettier` so formatting cannot change it.

**Bundled rather than fetched, deliberately.** A raster tile request carries a viewport,
and this system's viewport is derived from case evidence — so a tile service would learn
where the photographs were taken. Serving the geometry from the application's own origin
means the only request the map makes carries nothing derived from the case. Licence and
size rationale, and the measured resolution trade-off between 110m/50m/10m, are in
`frontend/public/basemap/PROVENANCE.md`.

### Declined, on measurement

**`maplibre-gl` 6.13.0 — BSD-3-Clause — not used.** Recorded here because the refusal is
not a licence one: MapLibre is available, permissively licensed, and *can* render a style
with no tile source, so the privacy objection alone never disqualified it. At 26.7 MB
unpacked across 19 dependencies and requiring WebGL, it is the wrong dependency for an
appliance built to run air-gapped on constrained hosts, in a lab VM without GPU
acceleration, or in a printed report — where a WebGL canvas is a blank rectangle. The
projection it would have replaced is hand-written and tested without a DOM.

### Not pinned

The remaining Phase 9 fixture sets (fingerprint, deepfake, gunshot)
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
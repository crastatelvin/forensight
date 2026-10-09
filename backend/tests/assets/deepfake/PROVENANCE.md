# Deepfake fixtures — P9.B3

24 images: **8 generated, 16 genuine.** Used to measure whether an AI-image detector
actually discriminates on material this repository controls.

## The licence reasoning, which is the reason this directory is small

Source: [`OwensLab/CommunityForensics`](https://huggingface.co/datasets/OwensLab/CommunityForensics),
split `PublicEval`, shard `HFCF_PublicEval_0.parquet`.

That dataset is **CC BY 4.0 qualified "for research purposes only"**, and that
qualification is **deliberately not relied on**. The licence of a specific image is set by
**the model that generated it**, not by the dataset it ships in, and the dataset publishes
exactly that table in `data/metadata/eval_set_metadata.csv`. So every image here is
selected on its **generator's** licence:

| Generator | Licence | Used |
|---|---|---|
| **FLUX-schnell** | **Apache 2.0** | **yes — 8 images** |
| **kandinsky_2_2** | **Apache 2.0** | not present in this shard |
| Midjourney v5/v6, DALL·E 2/3 | commercial ToS | no |
| Firefly Image 2/3 | Adobe GenAI ToS | no |
| Ideogram v1/v2 | Ideogram ToS | no |
| Imagen 3 | Google ToS | no |
| FLUX.1-dev | non-commercial | no |
| LCM (×3), DeciDiffusion v2 | OpenRAIL++ | no |
| GALIP, Hourglass | MIT | no |
| DFGAN | CC BY-NC-SA 4.0 | no |
| stable-cascade | -nc-community | no |

**Only the Apache-2.0 generator is used, and that is a narrowing with a measured cost.**
Of the 22 generators in the evaluation set, exactly two are Apache-2.0, and this shard
contains one of them. That is why the set is 8 images rather than 30, and why the measured
recall below is lower than the unrestricted multi-generator sweep recorded in
`CHECKLIST.md`. A single-generator fixture set is a weaker claim, and it is preferred to a
broader one resting on a licence nobody checked.

`manifest.json` records the generator, its licence, `real_source` and a sha256 for every
image, and `scripts/fixtures_deepfake.json` pins them so they pass the same gate as every
other fixture.

## What the detector does on these

`buildborderless/CommunityForensics-DeepfakeDet-ViT` (MIT), digest-pinned in
`scripts/fetch_models.sh`, with **its own** front end — CLIP normalisation, shortest edge
440, centre-crop 384, **sigmoid** output:

| | scores | flagged at 0.5 |
|---|---|---|
| generated (FLUX-schnell) | 0.0343 – 1.0000 | **6 / 8** |
| genuine (LAION, paired in the same shard) | 0.0000 – 0.0031 | **0 / 16** |

95% Wilson intervals: recall **[0.409, 0.929]**, false positives **[0.000, 0.194]**.

**The two misses are real and are not explained away.** One is a very dark image
(mean luma 15.5), but brightening it does not recover the score — 0.0883 rises only to
0.3426 at mean 120 — so it is a genuine blind spot on those images, not a low-light
artefact of the fixture. Face detection does not explain it either: a flagged image also
has zero detected faces. Both misses are kept rather than dropped, because removing a
sample after seeing its score is the selection error this repository has now rejected five
times.

## What this does not establish

- **One generator.** FLUX-schnell only. The unrestricted read-only sweep spanning DFGAN,
  FLUX-schnell and Midjourney measured **29/30**; the licence-clean set measures 6/8.
  Breadth across generators is exactly what the licence constraint costs.
- **16 genuine images.** That bounds the false-positive rate at 0.194, not at zero.
- **512×512 whole images.** The detector is face-centric and its own recipe centre-crops
  away the subject of a tall portrait — measured on `turing_a.jpg`, the face centre sits at
  y=97 while the crop window starts at y=101. That is the vendor's documented recipe
  losing a face by four pixels, and it is why a portrait-oriented front end is a
  prerequisite rather than a refinement.
- **GANs and pixel-diffusion models are untested here.** The excluded licence list
  contains DFGAN (GAN) and Hourglass (pixel diffusion), so the set covers one latent
  diffusion model and nothing else.

## Regenerating

Needs `pyarrow` and roughly 3.4 GB of disk for the shard.

```bash
curl -sL "https://huggingface.co/datasets/OwensLab/CommunityForensics/resolve/main/data/publicEval/HFCF_PublicEval_0.parquet" -o pe0.parquet
python scripts/extract_deepfake_fixtures.py pe0.parquet tests/assets/deepfake
```

The extractor **verifies the per-generator licences against the dataset's own metadata
before extracting anything**, and refuses if an upstream licence changes — a fixture set
committed on a licence that later changed would be a breach nothing in the pipeline would
catch. Images are committed whole rather than face-cropped, so that a front-end change
cannot masquerade as a fixture change.

`HFCF_PublicEval_0.parquet` is not committed: 3.4 GB against 2 MB of JPEGs.
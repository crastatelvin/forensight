# Deepfake fixtures — P9.B3

48 images: **24 generated, 24 genuine.** Used to measure whether an AI-image detector
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
| **FLUX-schnell** | **Apache 2.0** | **yes — 12 images** (PublicEval shard 0) |
| **kandinsky_2_2** | **Apache 2.0** | **yes — 12 images** (PublicEval shard 4) |
| Midjourney v5/v6, DALL·E 2/3 | commercial ToS | no |
| Firefly Image 2/3 | Adobe GenAI ToS | no |
| Ideogram v1/v2 | Ideogram ToS | no |
| Imagen 3 | Google ToS | no |
| FLUX.1-dev | non-commercial | no |
| LCM (×3), DeciDiffusion v2 | OpenRAIL++ | no |
| GALIP, Hourglass | MIT | no |
| DFGAN | CC BY-NC-SA 4.0 | no |
| stable-cascade | -nc-community | no |

**Only the two Apache-2.0 generators are used, and that is a narrowing with a measured
cost.** Of the 22 generators in the evaluation set, exactly two are Apache-2.0 — so 20 of
22 model families are excluded, including the GAN and pixel-diffusion ones. That is why the
set is 24 generated images across **two latent-diffusion models** rather than the 30 across
three architectures the unrestricted read-only sweep in `CHECKLIST.md` used. A narrower
claim, preferred to a broader one resting on licences nobody checked.

**The two permitted generators are not in the same shard.** FLUX-schnell is in
`HFCF_PublicEval_0` and kandinsky_2_2 in `HFCF_PublicEval_4`; shards 1, 2, 3 and 5 hold
only commercial-ToS, OpenRAIL++, MIT or NC generators. The generator list was established
from each shard's trailing metadata columns rather than by downloading 14 GB to find out.

`manifest.json` records the generator, its licence, `real_source` and a sha256 for every
image, and `scripts/fixtures_deepfake.json` pins them so they pass the same gate as every
other fixture.

## What the detector does on these

`buildborderless/CommunityForensics-DeepfakeDet-ViT` (MIT), digest-pinned in
`scripts/fetch_models.sh`, with **its own** front end — CLIP normalisation, shortest edge
440, centre-crop 384, **sigmoid** output:

| Population | scores | flagged at 0.5 |
|---|---|---|
| FLUX-schnell (generated) | 0.0343 – 1.0000 | **10 / 12** |
| kandinsky_2_2 (generated) | 0.0063 – 1.0000 | **10 / 12** |
| genuine (LAION, paired in the same shards) | 0.0000 – 0.0031 | **0 / 24** |

95% Wilson intervals: recall **[0.641, 0.933]**, false positives **[0.000, 0.138]**.
Recall is consistent across both generators, which is the useful part: the rate is not one
model carrying the average.

**The four misses are real and are not explained away.** They score 0.0063, 0.0343, 0.0883
and 0.3246, while 20 of the 24 generated images score **0.58 or above** — the distribution
is strongly bimodal rather than a smear across the threshold. Nothing measurable separates
the misses: their luma (15.5–156), contrast (31.8–68.0) and high-frequency energy (332–1128)
all overlap the flagged images. Brightening the darkest miss does not recover it (0.0883
reaches only 0.3426 at mean luma 120), and face detection does not explain it either — a
flagged image also has zero detected faces. **All four are kept**, because removing samples
after seeing their scores is the selection error this repository has now rejected five times.

## What this does not establish

- **Two generators, both latent diffusion.** FLUX-schnell and kandinsky_2_2. The
  unrestricted read-only sweep spanning DFGAN (GAN), FLUX-schnell and Midjourney measured
  **29/30**; the licence-clean set measures 20/24. **GANs and pixel-diffusion models are
  untested** — the only ones in the eval set are CC BY-NC-SA 4.0 and MIT, and the licence
  constraint excludes both. So nothing here says how the detector behaves on a GAN face.
- **24 genuine images, all LAION web photographs.** That bounds the false-positive rate at
  0.138, not at zero — and none of them is a scanned portrait or a document, so it says
  nothing about those.
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
base="https://huggingface.co/datasets/OwensLab/CommunityForensics/resolve/main/data/publicEval"
curl -sL "$base/HFCF_PublicEval_0.parquet" -o pe0.parquet   # FLUX-schnell
curl -sL "$base/HFCF_PublicEval_4.parquet" -o pe4.parquet   # kandinsky_2_2
python scripts/extract_deepfake_fixtures.py tests/assets/deepfake pe0.parquet pe4.parquet
```

Needs roughly 7 GB for both shards, and `pyarrow`. Quotas are global across the shards
supplied, so passing one shard yields a single-generator set — which the extractor says
out loud rather than reporting the smaller number as if it were the intended one.

The extractor **verifies the per-generator licences against the dataset's own metadata
before extracting anything**, and refuses if an upstream licence changes — a fixture set
committed on a licence that later changed would be a breach nothing in the pipeline would
catch. Images are committed whole rather than face-cropped, so that a front-end change
cannot masquerade as a fixture change.

The source shards are not committed: 6.8 GB against the JPEGs. Re-running produces
identical sha256 digests per fixture, verified.
"""Extract a deepfake fixture set from `OwensLab/CommunityForensics` PublicEval.

**Licence reasoning, because it is the whole reason this script filters.**

`OwensLab/CommunityForensics` is CC BY 4.0 but qualified "for research purposes only".
So the dataset-level licence is NOT relied on. What is relied on is the **per-generator**
licence the dataset ships in `data/metadata/eval_set_metadata.csv`, because the licence
of a specific image is set by the model that produced it:

    FLUX-schnell        Apache 2.0            <- usable
    kandinsky_2_2       apache 2.0            <- usable
    LCM_lora_*          OpenRAIL++            <- excluded, carries use restrictions
    GALIP / Hourglass   MIT                   <- excluded, not Apache 2.0
    DFGAN               cc-by-nc-sa 4.0       <- excluded, refused by the fixture gate
    Midjourney/Adobe/
    Google/Ideogram/
    OpenAI              commercial ToS        <- excluded, not redistributable
    FLUX-dev            non-commercial        <- excluded
    stable_cascade      -nc-community         <- excluded

**Only the two Apache-2.0 generators are extracted.** That is a deliberate narrowing: it is
the only set where the per-image licence is unambiguous without also ruling on whether
"research purposes only" constrains redistribution, and a fixture committed on a licence
reading nobody has checked is exactly the failure this project keeps catching.

Real (label 0) images from the same shards are the negatives. `PublicEval` pairs its
generated images with COCO and FFHQ specifically so the pairing is licence-compliant, and
the metadata records `real_source` per row so provenance can be stated per fixture.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import pathlib
import sys
from collections import Counter, defaultdict

import pyarrow.parquet as pq
from PIL import Image

#: Per-generator licences, copied from the dataset's own metadata file. Asserted against
#: the fetched CSV so a licence change upstream cannot silently alter what is committed.
PERMITTED_MODELS = {
    "FLUX-schnell": "Apache 2.0",
    "kandinsky_2_2": "apache 2.0",
}
PERMITTED_LICENCE_FRAGMENTS = ("apache 2.0", "apache-2.0")

DATASET = "OwensLab/CommunityForensics"
DATASET_LICENCE = "cc-by-4.0"
DATASET_LICENCE_NOTE = (
    "Dataset is CC BY 4.0 qualified 'for research purposes only'. That qualification is NOT "
    "relied upon: each committed image is selected on the per-generator licence recorded in "
    "the dataset's data/metadata/eval_set_metadata.csv, and only Apache-2.0 generators are "
    "used."
)

PER_MODEL = 8  # generated images per permitted generator
PER_REAL = 16  # genuine images for the negative side


def fetch_metadata() -> list[dict[str, str]]:
    """The dataset's own per-generator licence table."""
    import urllib.request

    url = f"https://huggingface.co/datasets/{DATASET}/resolve/main/data/metadata/eval_set_metadata.csv"
    with urllib.request.urlopen(url, timeout=60) as handle:  # noqa: S310 - fixed https host
        return list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")))


def verify_licences(rows: list[dict[str, str]]) -> None:
    """Refuse if upstream licences no longer match what this script assumes.

    A fixture committed on a licence that later changed would be a licence breach nothing
    in the pipeline would catch, so the assumption is checked rather than trusted.
    """
    found = {r["Model"]: r["License"] for r in rows}
    problems = []
    for model, expected in PERMITTED_MODELS.items():
        actual = found.get(model)
        if actual is None:
            problems.append(f"{model}: absent from the dataset metadata")
        elif actual.strip().lower() not in PERMITTED_LICENCE_FRAGMENTS:
            problems.append(f"{model}: licence is now {actual!r}, expected {expected!r}")
    if problems:
        raise SystemExit("licence check failed, refusing to extract:\n  " + "\n  ".join(problems))
    print(f"  licence check passed for {sorted(PERMITTED_MODELS)}")


def extract(shard: pathlib.Path, out_dir: pathlib.Path) -> dict[str, list[dict[str, object]]]:
    """Pull permitted generators' outputs and the real images, as small JPEGs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    by_model: dict[str, int] = defaultdict(int)
    real_count = 0
    manifest: list[dict[str, object]] = []

    handle = pq.ParquetFile(str(shard))
    columns = ["image_name", "image_data", "label", "model_name", "real_source", "resolution"]
    for batch in handle.iter_batches(batch_size=256, columns=columns):
        for row in batch.to_pylist():
            want_generated = row["label"] == 1 and row["model_name"] in PERMITTED_MODELS
            if not want_generated and not (row["label"] == 0 and real_count < PER_REAL):
                continue
            if not want_generated and by_model and real_count >= PER_REAL:
                continue
            try:
                image = Image.open(io.BytesIO(row["image_data"]))
                image.load()
            except Exception:  # noqa: BLE001, S112 - a corrupt row is skipped, not fatal
                continue

            if want_generated:
                if by_model[row["model_name"]] >= PER_MODEL:
                    continue
                # A face crop is what the model actually consumes, but commit the whole
                # image: cropping at fixture time would bake a preprocessing choice into
                # the evidence and make a front-end change look like a fixture change.
                image = image.convert("RGB")
                stem = row["model_name"].replace("/", "_")
                name = f"gen_{stem}_{by_model[row['model_name']]:02d}.jpg"
                by_model[row["model_name"]] += 1
                provenance = {
                    "file": name,
                    "label": "generated",
                    "generator": row["model_name"],
                    "generator_licence": PERMITTED_MODELS[row["model_name"]],
                    "real_source": row["real_source"],
                    "resolution": row["resolution"],
                }
            else:
                image = image.convert("RGB")
                name = f"real_{real_count:02d}.jpg"
                real_count += 1
                provenance = {
                    "file": name,
                    "label": "real",
                    "generator": None,
                    "generator_licence": None,
                    "real_source": row["real_source"],
                    "resolution": row["resolution"],
                }

            image.save(out_dir / name, "JPEG", quality=95, optimize=True)
            provenance["sha256"] = hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
            provenance["bytes"] = (out_dir / name).stat().st_size
            manifest.append(provenance)

        if (
            real_count >= PER_REAL
            and all(v >= PER_MODEL for v in by_model.values())
            and all(m in by_model for m in PERMITTED_MODELS)
        ):
            break

    return {
        "manifest": manifest,
        "counts": {
            "generated": sum(1 for m in manifest if m["label"] == "generated"),
            "real": sum(1 for m in manifest if m["label"] == "real"),
            "by_generator": dict(
                Counter(m["generator"] for m in manifest if m["label"] == "generated")
            ),
        },
    }


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: extract_deepfake_fixtures.py <shard.parquet> <out_dir>", file=sys.stderr)
        return 2
    shard, out_dir = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    if not shard.is_file():
        print(f"shard not found: {shard}", file=sys.stderr)
        return 1

    print("  verifying per-generator licences against the dataset's own metadata...")
    verify_licences(fetch_metadata())

    result = extract(shard, out_dir)
    counts = result["counts"]
    print(
        f"  extracted {counts['generated']} generated ({counts['by_generator']}) "
        f"and {counts['real']} real -> {out_dir}"
    )
    if counts["generated"] == 0 or counts["real"] == 0:
        print("  incomplete: need both populations", file=sys.stderr)
        return 1
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "dataset": DATASET,
                "dataset_licence": DATASET_LICENCE,
                "dataset_licence_note": DATASET_LICENCE_NOTE,
                "shard": shard.name,
                "count": result["counts"],
                "fixtures": result["manifest"],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"  manifest written to {out_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

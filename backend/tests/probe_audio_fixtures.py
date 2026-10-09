"""What the audio fixtures actually are — measured, not read off their filenames.

**This file exists because two of these clips are not what their titles claim**, and the
only thing that revealed it was running an audio classifier over them:

- **`seg_gunshots.wav`** — titled gunfire, and gunfire it is. The model says
  `Sound effect`, then `Slap, smack`; `Gunshot, gunfire` ranks **third**, P=0.077.
- **`seg_fireworks.wav`** — titled fireworks. `Walk, footsteps` first (0.102) with
  `Fireworks` second (0.095): a coin toss, but the right family. P(gunfire)=0.030.
- **`seg_machinegun.wav`** — titled machine gun. **`Music` at 0.647.** It is a music
  file. P(gunfire)=0.0001.
- **`seg_thunder.wav`** — titled thunder. **`Speech` at 0.82.** It is a spoken
  recording. P(gunfire)=0.0000.
- **`seg_lightning.wav`** — `Rain on surface`. P(gunfire)=0.0003.
- **`seg_speech_control.wav`** — the control, and it works: **`Speech` at 0.885.**

That is the finding. **Audio fixture filenames are not evidence of content**, and a
pipeline that trusted them would have shipped a gunshot detector "validated" on a music
track and a spoken recording — both of which pass a licence gate, both of which contain
the word.

It is the same failure as the `LL-Q1860 …-gunshot.wav` cluster found earlier in this
phase: Wikipedia pronunciation files, correctly licensed, that are a person *saying* the
word "gunshot". Here it is worse, because these are not mislabelled by Commons'
convention — they are mislabelled by whoever uploaded them.

## The measurement, and why it needs an oracle

The classification above was produced by the **reference** AST feature extractor from
`transformers`, not by this project's own front end. That distinction matters: the
front end written alongside this (`app/analysis/audio_features.py`) agrees with the
reference *qualitatively* — both rank fireworks and bang high on the gunfire clip, and
both call the machine-gun file music — but differs numerically (mean absolute difference
~1.5 after normalisation), and a difference that size would change the argmax. **So the
numbers quoted here are the reference's, and no conclusion here rests on the hand-built
front end.**

The reference was run in a throwaway virtualenv holding `transformers`, `torch` and
`onnxruntime`, **none of which this project depends on**. (`torch` is not optional there:
`transformers`' AST feature extractor imports it, and without it the probe dies with a bare
`NameError: name 'torch' is not defined` — which is exactly what happened the first time
this recipe was written down.) Reproducing it adds no project dependency:

```bash
python -m venv /tmp/oracle
/tmp/oracle/bin/pip install transformers onnxruntime torch
/tmp/oracle/bin/python backend/tests/probe_audio_fixtures.py
```

The labels come from `.models/ast_labels.json`, written from the model's own
`config.json` so the class names are not transcribed by hand — a transposed label list
would quietly rename every finding below.

## The model

`Xenova/ast-finetuned-audioset-10-10-0.4593`, an ONNX export of the MIT-licensed
`MIT/ast-finetuned-audioset-10-10-0.4593`. 527 AudioSet classes; `Gunshot, gunfire` is
index 427, with `Machine gun` at 428 and `Bang` beside them. Weights are **not** committed:
`sha256 6ff353b1e809da25f900b54494fd4ac12a6fc7db1e25195e238f21be125bb4bd`, 346 MB.

## Why there is no `audio-gunfire` module here yet

Because **one genuine gunfire clip is not a measurement.** With n=1 positive,
P(gunfire) 0.077 against 0.030 for the next-highest clip is a factor of 2.6 with a single
sample on one side of it — not a separation, and certainly not a threshold. Shipping a
detector on that would be the same defect as the deepfake detector that flagged 8 of 8
genuine photographs as fake: a number shaped like a finding, resting on nothing.

The blocker is therefore *not* the technique, which is available, permitted and working.
It is that four corpora (Wikimedia Commons, archive.org, Freesound, DVIDS) contain
between them **one** gunfire recording under a licence this project accepts, and that
finding could not be corroborated against a second source.
"""

from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

ASSETS = pathlib.Path(__file__).resolve().parents[1] / "tests" / "assets" / "audio"
MODEL = pathlib.Path(__file__).resolve().parents[1] / ".models" / "ast-audioset.onnx"

#: The AudioSet class this question is about, by name rather than by index — an
#: index is a claim about someone else's file, and a name is not.
GUNFIRE_CLASS = "Gunshot, gunfire"

#: What each fixture is *actually*, measured with the reference front end.
#:
#: Written as an expectation so this is a check rather than a report: if a fixture is ever
#: replaced, or the model changes, the expectation moves and fails loudly instead of the
#: finding quietly becoming folklore.
#:
#: **Asserted on the top class and on `P(gunfire)` together, because either alone is
#: weak.** Top class alone would call the fireworks clip `Walk, footsteps` (0.102) when
#: `Fireworks` is second at 0.095 — a coin toss between two classes, not a finding. And
#: `P(gunfire)` alone would pass on a fixture that is nothing but silence.
EXPECTED: dict[str, tuple[str, float, float]] = {
    # The one genuine gunfire clip. Gunfire is *ranked third*, behind `Sound effect` and
    # `Bang` — which is why this is not shipped as a detector.
    "seg_gunshots.wav": ("Sound effect", 0.03, 0.20),
    "seg_fireworks.wav": ("Walk, footsteps", 0.005, 0.10),
    "seg_lightning.wav": ("Rain on surface", 0.0, 0.01),
    "seg_machinegun.wav": ("Music", 0.0, 0.01),
    "seg_speech_control.wav": ("Speech", 0.0, 0.01),
    "seg_thunder.wav": ("Speech", 0.0, 0.01),
}


def probe(reference: bool = False) -> int:
    """Classify every fixture. `reference=True` uses `transformers` (needs that venv).

    Returns a process exit code, so it is usable as a check as well as a report.
    """
    import wave

    import onnxruntime as ort

    labels = {
        int(k): v for k, v in json.loads((MODEL.parent / "ast_labels.json").read_text()).items()
    }
    session = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])

    failures = 0
    for name, (top, low, high) in sorted(EXPECTED.items()):
        path = ASSETS / name
        if not path.is_file():
            print(f"{name:24} MISSING")
            failures += 1
            continue
        with wave.open(str(path), "rb") as handle:
            pcm = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
            rate = handle.getframerate()
        logits = session.run(None, {"input_values": _reference_features(pcm, rate)})[0][0]
        exp = np.exp(logits - logits.max())
        probs = exp / exp.sum()
        ranked = np.argsort(probs)[::-1]
        best = labels[int(ranked[0])]
        gunfire = float(probs[[i for i, v in labels.items() if v == GUNFIRE_CLASS][0]])
        ok = best == top and low <= gunfire <= high
        failures += 0 if ok else 1
        print(
            f"{name:24} {'ok ' if ok else 'CHANGED'} top={best:22} "
            f"P(gunfire)={gunfire:.4f} (expected {top}, {low}-{high})"
        )
    return failures


def _reference_features(pcm: np.ndarray, rate: int) -> np.ndarray:
    from transformers import ASTFeatureExtractor  # noqa: PLC0415 - needs the oracle venv

    extractor = ASTFeatureExtractor.from_pretrained("MIT/ast-finetuned-audioset-10-10-0.4593")
    audio = pcm.astype(np.float32) / 32768.0
    return extractor(audio, sampling_rate=rate, return_tensors="np")["input_values"].astype(
        np.float32
    )


if __name__ == "__main__":
    sys.exit(probe())

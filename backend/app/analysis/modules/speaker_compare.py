"""Speaker comparison from ECAPA-TDNN embeddings — **a verification aid, not an ID.**

Two recordings of the same voice share more than any two recordings of different voices
share, and measuring that is a real forensic technique. What it is not is an
identification: this compares two recordings *you already have*, it never searches a
population, and it cannot tell you *who* anyone is.

**Measured, and the headline is that it does not separate on these fixtures.** Eight
public-domain LibriVox recordings — three readers, multiple books each — through the
pinned ECAPA-TDNN (`Wespeaker/wespeaker-voxceleb-ecapa-tdnn512`, Apache-2.0):

| | cosine |
|---|---|
| same reader, different books | 0.328 – 0.911 |
| different readers | 0.106 – 0.841 |

**The ranges overlap, and the overlap is one file.** `kara_b` (*The Secret Garden*)
scores 0.328–0.607 against the same reader's other books and **0.809–0.841 against a
different reader** — it is further from its own speaker than from another person's. Excluding it
narrows the overlap but leaves the ranges touching, by **0.0004**, which is a coin flip
rather than a threshold.

**The cause is the fixtures, and it is worth stating because it is the kind of mistake
that silently invalidates an evaluation.** LibriVox readings are *dramatic readings*:
one narrator performs several characters. *The Secret Garden* is read in character
voices, so `kara_b` genuinely does sound like a different person, and no speaker
embedding should match it. Speaker verification needs one person's voice recorded
twice; a multi-character performance is a different task, and using it as a fixture
measures the wrong thing. **The fixture is retained and asserted as a failure** rather
than dropped: dropping it would produce a green test resting on a selection made after
seeing the result.

**What this means for use.** The score is a reproducible similarity between two
recordings the examiner already holds. It is not a likelihood, not a probability, and
the absence of a usable threshold on eight real recordings means **this deployment has
no calibrated operating point for this technique**. A match here is a prompt to look at
more audio, and a non-match is not evidence of different speakers.

**Not usable on phone or courtroom recordings from this evidence.** Channel, codec,
noise and language shift all move these numbers, and none of them are controlled here.

**Not biometric identification.** ECAPA embeddings have been shown to be recoverable
from short audio; they are not stored by this system (only the derived comparison
score is), and the score should still be handled as personal data.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import wave
from typing import Any

import numpy as np

from app.analysis.types import (
    AnalysisNotApplicable,
    ModelChecksumError,
    ModuleSpec,
    Tier,
    build_result,
)

ALGORITHM_VERSION = "1.0.0"
TIER = Tier.B

MODEL_KEY = "speaker/ecapa/voxceleb_ECAPA512.onnx"
MODEL_SHA256 = "f901ae3571912bad4e11e157744cb8b7d77a0b46923b83c410bdbb6c77d69736"
MODEL_URL = "https://huggingface.co/Wespeaker/wespeaker-voxceleb-ecapa-tdnn512/resolve/main/voxceleb_ECAPA512.onnx"
MODEL_TERMS = "Apache-2.0 (Wespeaker), trained on VoxCeleb2"

#: Front-end settings, all read out of the model's own `config.yaml`:
#: `frame_length: 25`, `frame_shift: 10`, `num_mel_bins: 80`.
#:
#: Pinned here rather than guessed because a *wrong* front-end does not fail — it
#: returns embeddings from a pipeline the model was never trained on, which still look
#: plausible and still separate somewhat. That is the failure mode this whole comment
#: exists to prevent, and it is why the separation above was measured rather than
#: assumed.
SAMPLE_RATE = 16000
FRAME_LENGTH_MS = 25
FRAME_SHIFT_MS = 10
N_MELS = 80
FFT_SIZE = 512
#: SpeechBrain's log offset — half the smallest float normal. Matches
#: `torch.finfo(torch.float).eps` in `speechbrain.lobes.features.Fbank`.
LOG_OFFSET = 1.1921e-07

#: Minimum audio. Below this the embedding is dominated by the recording's start-up.
MIN_SECONDS = 5.0

#: Decision bar in cosine similarity over ECAPA embeddings.
#:
#: **Placed above the worst measured different-reader pair, not between the ranges.**
#: The two ranges overlap (0.665 same against 0.874 different), so there is no value
#: that separates them. 0.88 is chosen as "higher than anything a different reader
#: produced here", which is a statement about the top of the observed range and nothing
#: more. It is deliberately conservative: a bar inside the overlap would be a number
#: chosen to look decisive.
THRESHOLD = 0.88

DISCLAIMER = (
    "Speaker comparison from ECAPA-TDNN embeddings — the similarity between two "
    "recordings the examiner already holds. This is verification (1:1), NOT "
    "identification (1:N): nothing here says who anyone is, and this module cannot "
    "search a population. It is not a likelihood or a probability. NO CALIBRATED "
    "OPERATING POINT EXISTS for this technique on this evidence: measured over eight "
    "public-domain readings, same-reader similarity runs 0.328-0.911 while "
    "different-reader similarity reaches 0.841, so the ranges overlap and no "
    "threshold separates them. The bar above is higher than any different-reader pair "
    "observed, which is a fact about that sample and not a validated rule. A match is a "
    "prompt to examine more audio; a non-match is NOT evidence that two recordings are "
    "of different people — recording conditions, codec, channel, language and noise "
    "all move this number, and a multi-character dramatic reading will not match its "
    "own narrator. Treat any score here as a lead, never as a finding."
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "not identification and not a likelihood: a similarity between two given recordings",
    "no calibrated operating point exists here — same-reader and different-reader ranges "
    "overlap (0.328-0.911 against up to 0.841) on the eight public-domain readings this "
    "was measured on, so a non-match is not evidence of a different speaker",
    "multi-character dramatic readings do not match their own narrator, because the "
    "other characters are different voices; one of the eight fixtures is exactly this "
    "case and is retained as a known failure rather than dropped",
    "highly sensitive to recording conditions: channel, codec, sample rate, background "
    "noise and language all move the score, and none are controlled by this module",
    "phone and courtroom recordings are outside anything measured here — these fixtures "
    "are clean studio-read public-domain audio, which is the easiest case available",
    "short audio is unreliable and is refused below five seconds rather than scored",
    "speaker embeddings are known to be recoverable from short audio; this system stores "
    "only the derived score and never the embedding or the audio, but the score is "
    "still personal data",
    "accepts WAV only: decoding compressed audio needs an external decoder, and "
    "shelling out to one from an analyzer is a dependency this module does not take",
)

_mel_cache: np.ndarray | None = None


def _mel(hz: float) -> float:
    """HTK mel scale, as SpeechBrain's filterbank uses it."""
    return float(2595.0 * np.log10(1.0 + hz / 700.0))


def _mel_filterbank() -> np.ndarray:
    """The 80 x 257 mel filterbank, cached — it depends only on the constants."""
    global _mel_cache
    if _mel_cache is not None:
        return _mel_cache
    n_bins = FFT_SIZE // 2 + 1
    freqs = np.linspace(0.0, SAMPLE_RATE / 2, n_bins)

    edges = 700.0 * (
        10.0 ** (np.linspace(_mel(0.0), _mel(SAMPLE_RATE / 2), N_MELS + 2) / 2595.0) - 1.0
    )
    fb = np.zeros((N_MELS, n_bins), dtype=np.float64)
    for index in range(N_MELS):
        low, mid, high = edges[index], edges[index + 1], edges[index + 2]
        rising = (freqs > low) & (freqs <= mid)
        falling = (freqs > mid) & (freqs <= high)
        fb[index, rising] = (freqs[rising] - low) / (mid - low)
        fb[index, falling] = (high - freqs[falling]) / (high - mid)
    _mel_cache = fb
    return fb


def read_wav(data: bytes) -> np.ndarray:
    """16 kHz mono float32 from a WAV file.

    Rejects rather than resamples. A module that silently resampled would score a 44.1
    kHz file against a 16 kHz one and produce a number that looked comparable and was
    not — and "refused: wrong sample rate" is a fact the examiner can act on.
    """
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            if handle.getnchannels() != 1:
                raise AnalysisNotApplicable(
                    f"audio has {handle.getnchannels()} channels; this comparison is "
                    "mono-only, because channel handling is not a decision this module "
                    "should make silently"
                )
            rate = handle.getframerate()
            if rate != SAMPLE_RATE:
                raise AnalysisNotApplicable(
                    f"audio is {rate} Hz; ECAPA was trained on {SAMPLE_RATE} Hz. "
                    "Resample deliberately and re-run rather than have this module "
                    "guess at the conversion."
                )
            if handle.getsampwidth() != 2:
                raise AnalysisNotApplicable(
                    f"audio is {handle.getsampwidth() * 8}-bit; 16-bit PCM is expected"
                )
            raw = handle.readframes(handle.getnframes())
    except AnalysisNotApplicable:
        raise
    except Exception as exc:  # noqa: BLE001 - undecodable input is a refusal
        raise AnalysisNotApplicable(f"the evidence did not decode as a WAV file ({exc})") from exc

    samples: np.ndarray = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    seconds = len(samples) / SAMPLE_RATE
    if seconds < MIN_SECONDS:
        raise AnalysisNotApplicable(
            f"audio is {seconds:.1f}s; at least {MIN_SECONDS}s is needed for the "
            "embedding to be dominated by the voice rather than the recording's start-up"
        )
    return samples


def filterbank(wav: np.ndarray) -> np.ndarray:
    """Log-mel filterbank features, `[frames, 80]`."""
    win = int(SAMPLE_RATE * FRAME_LENGTH_MS / 1000)
    hop = int(SAMPLE_RATE * FRAME_SHIFT_MS / 1000)
    n_frames = 1 + max(0, (len(wav) - win)) // hop
    index = np.arange(win)[None, :] + hop * np.arange(n_frames)[:, None]
    index = np.minimum(index, len(wav) - 1)
    power = np.abs(np.fft.rfft(wav[index] * np.hamming(win), n=FFT_SIZE)) ** 2
    out: np.ndarray = np.log(np.maximum(power @ _mel_filterbank().T, 0.0) + LOG_OFFSET).astype(
        np.float32
    )
    return out


def embed(wav: np.ndarray, model_bytes: bytes | None) -> np.ndarray:
    """A L2-normalised ECAPA embedding."""
    if not model_bytes:
        raise ModelChecksumError(
            "speaker-compare requires verified ECAPA weights; refusing to run without them"
        )
    actual = hashlib.sha256(model_bytes).hexdigest()
    if actual != MODEL_SHA256:
        raise ModelChecksumError(
            f"ECAPA weights hash to {actual[:16]}… but {MODEL_SHA256[:16]}… is pinned"
        )

    import onnxruntime as ort

    session = ort.InferenceSession(model_bytes, providers=["CPUExecutionProvider"])
    features = filterbank(wav)
    vector = session.run(["embs"], {"feats": features[None, :, :]})[0][0].astype(np.float64)
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        raise AnalysisNotApplicable("the embedding is empty; nothing was measured")
    normalised: np.ndarray = vector / norm
    return normalised


def _version() -> str:
    import onnxruntime as ort

    return f"{ALGORITHM_VERSION}+ort{ort.__version__}+numpy{np.__version__}"


VERSION = _version()


def run(
    data: bytes,
    params: dict[str, Any],
    *,
    model_bytes: bytes | None = None,
    reference_bytes: bytes | None = None,
    aux_models: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Compare two recordings of a voice."""
    _ = aux_models
    if reference_bytes is None:
        raise AnalysisNotApplicable(
            "no reference recording was supplied: speaker comparison needs two "
            "recordings, set params.reference_evidence_id to the second evidence id"
        )

    subject = embed(read_wav(data), model_bytes)
    reference = embed(read_wav(reference_bytes), model_bytes)
    similarity = float(np.dot(subject, reference))
    above = similarity >= THRESHOLD

    spec = ModuleSpec(
        name="speaker-compare",
        version=VERSION,
        tier=TIER,
        threshold=THRESHOLD,
        accepts=("audio/wav", "audio/x-wav"),
        model_key=MODEL_KEY,
        model_sha256=MODEL_SHA256,
        disclaimer=DISCLAIMER,
    )

    return build_result(
        spec,
        score=similarity,
        detail={
            "score_is": (
                "cosine similarity between two ECAPA-TDNN speaker embeddings — a "
                "verification (1:1) similarity, NOT an identification, NOT a "
                "likelihood and NOT a probability"
            ),
            "comparison_type": "one-to-one verification (never identification)",
            "similarity": round(similarity, 6),
            "embedding_dimension": int(subject.shape[0]),
            "reference_label": str(params.get("reference_label") or "unnamed"),
            "calibration": {
                "threshold_source": (
                    "measured on eight public-domain LibriVox readings: same-reader "
                    "0.328-0.911, different-reader up to 0.841"
                ),
                "calibrated_on_this_deployment": False,
                "ranges_separate": False,
                "note": (
                    "the two measured ranges OVERLAP, so no threshold separates them. "
                    "The bar is placed above the worst different-reader pair observed, "
                    "which is a statement about that sample rather than a validated "
                    "rule. A deployment needing a defensible operating point must "
                    "measure its own — on recordings from the population it actually "
                    "sees, including phone and courtroom audio, which these fixtures "
                    "are not."
                ),
            },
            "known_limitations": list(KNOWN_LIMITATIONS),
            "params": {k: v for k, v in params.items() if k != "reference_text"},
            "interpretation": (
                (
                    f"{similarity:.3f}, at or above the {THRESHOLD} bar. These two "
                    "recordings may be the same voice. That is a prompt to examine "
                    "more audio — not an identification, and not evidence that anyone "
                    "in either recording is any particular person."
                )
                if above
                else (
                    f"{similarity:.3f}, below the {THRESHOLD} bar. This is NOT evidence "
                    "that the two recordings are of different people: recording "
                    "conditions, codec, channel, language and noise all move this "
                    "number, a multi-character dramatic reading will not match its own "
                    "narrator, and no calibrated threshold exists for this technique "
                    "here. Read it as 'not shown to match'."
                )
            ),
        },
    )


spec = ModuleSpec(
    name="speaker-compare",
    version=VERSION,
    tier=TIER,
    threshold=THRESHOLD,
    accepts=("audio/wav", "audio/x-wav"),
    model_key=MODEL_KEY,
    model_sha256=MODEL_SHA256,
    params={
        "reference_evidence_id": {
            "type": "uuid",
            "description": (
                "The second recording to compare against, as an evidence id in the same "
                "case. Required — resolved case-scoped and hash-verified first."
            ),
        },
        "reference_label": {
            "type": "string",
            "description": "How to name the reference in the result payload.",
        },
    },
    disclaimer=DISCLAIMER,
)


class _SpeakerCompareModule:
    def __init__(self) -> None:
        self.spec = spec

    def __call__(
        self,
        data: bytes,
        params: dict[str, Any],
        *,
        model_bytes: bytes | None = None,
        aux_models: dict[str, bytes] | None = None,
        reference_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        return run(
            data,
            params,
            model_bytes=model_bytes,
            reference_bytes=reference_bytes,
            aux_models=aux_models,
        )


module = _SpeakerCompareModule()


def ffmpeg_available() -> bool:
    """Whether the fixture-derivation toolchain is present.

    Used only to build the test fixtures, never at analysis time: this module decodes
    WAV in-process and does not shell out. Reported so a missing `ffmpeg` is an
    explicit skip rather than a confusing failure inside a fixture test.
    """
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)

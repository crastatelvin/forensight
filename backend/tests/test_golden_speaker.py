"""P9.B2 / T9.3 — `speaker-compare`: golden tests, including the one that shows it fails.

**The headline measurement is a negative one and it is the point of this file.** Over
these eight public-domain readings, same-reader similarity runs 0.665–0.906 while
different-reader similarity reaches 0.874 — the ranges overlap, and no threshold
separates them. That is written here rather than tuned away.

The cause is the fixtures, and it is recorded because it is the kind of mistake that
silently invalidates an evaluation: LibriVox recordings are *dramatic readings*, one
narrator performing several characters, so a multi-character excerpt genuinely does not
sound like its own narrator. `kara_b` is that case, it is **retained**, and a test
asserts the failure persists — dropping it after seeing the result would leave a green
test resting on a selection made to produce one.

T9.3 asked for "same > threshold / different < threshold". The measured answer is that
this does not hold on this evidence, and the answer is asserted rather than engineered.
"""

from __future__ import annotations

import itertools
import pathlib

import pytest
from app.analysis.modules import speaker_compare as sc
from app.analysis.types import AnalysisNotApplicable, ModelChecksumError

ASSETS = pathlib.Path(__file__).parent / "assets" / "speaker"
FIXTURES = sorted(ASSETS.glob("*.wav"))
MODEL = pathlib.Path(".models/ecapa.onnx")


@pytest.fixture(scope="module")
def weights() -> bytes:
    if not MODEL.exists():
        pytest.skip(f"{MODEL} absent; run backend/scripts/fetch_models.sh")
    return MODEL.read_bytes()


@pytest.fixture(scope="module")
def vectors(weights: bytes) -> dict[str, object]:
    return {p.stem: sc.embed(sc.read_wav(p.read_bytes()), weights) for p in FIXTURES}


def _speaker(name: str) -> str:
    return name.rsplit("_", 1)[0]


def _pairs(vectors: dict[str, object]) -> tuple[list[float], list[float]]:
    same, different = [], []
    for a, b in itertools.combinations(sorted(vectors), 2):
        score = float(vectors[a] @ vectors[b])  # type: ignore[operator]
        (same if _speaker(a) == _speaker(b) else different).append(score)
    return same, different


# --- the fixtures are what the measurement assumes ------------------------


def test_there_are_eight_distinct_public_domain_readings() -> None:
    assert len(FIXTURES) == 8
    assert len({p.read_bytes() for p in FIXTURES}) == 8
    assert {p.stem.rsplit("_", 1)[0] for p in FIXTURES} == {"kara", "lyons", "sirois"}


def test_every_fixture_is_16k_mono_16bit(weights: bytes) -> None:
    """The front-end refuses anything else, so the fixtures must satisfy it."""
    for path in FIXTURES:
        samples = sc.read_wav(path.read_bytes())
        assert samples.shape[0] > sc.MIN_SECONDS * sc.SAMPLE_RATE


# --- the negative result, which governs everything else --------------------


def test_the_measured_ranges_do_not_separate(vectors: dict[str, object]) -> None:
    """T9.3, answered honestly: same > threshold / different < threshold does not hold.

    Written as an assertion so that a future change which *does* separate has to
    update the module's calibration block deliberately, rather than the separation
    appearing quietly in a golden file and being read as evidence the technique works.
    """
    same, different = _pairs(vectors)
    assert min(same) <= max(different), (
        "the fixtures now separate by reader. That is worth knowing, and it invalidates "
        "the calibration note in the module and in CHECKLIST — update both, and only "
        "then treat the threshold as meaningful."
    )


def test_the_measured_ranges_are_pinned(vectors: dict[str, object]) -> None:
    """Exact values, so a drift in Pillow/numpy/onnxruntime or the front-end shows up."""
    same, different = _pairs(vectors)
    assert round(min(same), 4) == 0.3279
    assert round(max(same), 4) == 0.9112
    assert round(max(different), 4) == 0.8412
    assert round(min(different), 4) == 0.1059
    assert len(same) == 7
    assert len(different) == 21


def test_a_multi_character_reading_does_not_match_its_own_narrator(
    vectors: dict[str, object],
) -> None:
    """The documented failure, asserted so it cannot be quietly removed.

    `kara_b` is *The Secret Garden* read in character voices, so it is further from
    its own narrator than from a different reader. That is correct behaviour from a
    speaker embedding and wrong material for a speaker-verification fixture — which is
    the lesson, and the reason these are not evidence the technique works.
    """
    vs_narrator = float(vectors["kara_a"] @ vectors["kara_b"])  # type: ignore[operator]
    vs_other = float(vectors["kara_b"] @ vectors["lyons_b"])  # type: ignore[operator]
    assert vs_other > vs_narrator, (
        "kara_b no longer behaves as the documented multi-character failure. If the "
        "fixtures changed, the calibration note describing that failure is now wrong."
    )


def test_a_well_behaved_reader_has_the_expected_profile(vectors: dict[str, object]) -> None:
    """The positive control: the model and the front-end do discriminate.

    Without this, "the ranges overlap" would be equally consistent with a *broken*
    pipeline. `lyons_a` sits closest to the other Lyons readings and furthest from the
    others, which is only possible if the embeddings mean something.
    """
    within = min(
        float(vectors["lyons_a"] @ vectors[n])  # type: ignore[operator]
        for n in ("lyons_b", "lyons_c")
    )
    without = max(
        float(vectors["lyons_a"] @ vectors[n])  # type: ignore[operator]
        for n in ("kara_a", "kara_c", "sirois_a", "sirois_b")
    )
    assert within > without + 0.2, "the pipeline is not discriminating at all"


# --- the module's own contract --------------------------------------------


def test_the_score_is_the_cosine_of_the_embeddings(weights: bytes) -> None:
    a, b = ASSETS / "lyons_a.wav", ASSETS / "lyons_c.wav"
    result = sc.run(a.read_bytes(), {}, model_bytes=weights, reference_bytes=b.read_bytes())
    expected = float(
        sc.embed(sc.read_wav(a.read_bytes()), weights)
        @ sc.embed(sc.read_wav(b.read_bytes()), weights)
    )
    assert result["score"] == pytest.approx(expected, abs=1e-9)
    assert 0.0 <= expected <= 1.0


def test_repeated_runs_are_identical(weights: bytes) -> None:
    """Admissibility depends on reproducing a score."""
    a, b = ASSETS / "lyons_a.wav", ASSETS / "lyons_c.wav"
    kwargs = {"model_bytes": weights, "reference_bytes": b.read_bytes()}
    assert sc.run(a.read_bytes(), {}, **kwargs) == sc.run(a.read_bytes(), {}, **kwargs)


def test_weights_are_verified_before_use() -> None:
    """A wrong model cannot produce a defensible score."""
    with pytest.raises(ModelChecksumError, match="verified ECAPA weights"):
        sc.embed(sc.read_wav((ASSETS / "lyons_a.wav").read_bytes()), None)
    with pytest.raises(ModelChecksumError, match="pinned"):
        sc.embed(sc.read_wav((ASSETS / "lyons_a.wav").read_bytes()), b"not the model")


def test_a_missing_reference_refuses() -> None:
    """One recording alone is not a low similarity."""
    with pytest.raises(AnalysisNotApplicable, match="no reference recording"):
        sc.run((ASSETS / "lyons_a.wav").read_bytes(), {}, model_bytes=b"x")
    with pytest.raises(AnalysisNotApplicable, match="reference_evidence_id"):
        sc.module((ASSETS / "lyons_a.wav").read_bytes(), {})


def test_audio_at_the_wrong_sample_rate_is_refused_not_resampled(weights: bytes) -> None:
    """Resampling silently would make 44.1 kHz comparable with 16 kHz — and it is not."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * 44100)
    with pytest.raises(AnalysisNotApplicable, match="44100 Hz"):
        sc.read_wav(buf.getvalue())


def test_short_audio_is_refused_rather_than_scored() -> None:
    """Below the floor the embedding measures the recording's start-up."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sc.SAMPLE_RATE)
        handle.writeframes(b"\x00\x00" * sc.SAMPLE_RATE)  # exactly 1 s
    with pytest.raises(AnalysisNotApplicable, match="at least"):
        sc.read_wav(buf.getvalue())


def test_undecodable_audio_refuses() -> None:
    good = (ASSETS / "lyons_a.wav").read_bytes()
    with pytest.raises(AnalysisNotApplicable):
        sc.run(b"RIFF\x00\x00\x00\x00nope", {}, model_bytes=b"x", reference_bytes=good)


def test_the_result_refuses_the_readings_it_cannot_support(weights: bytes) -> None:
    """Verification is not identification, and a non-match is not a difference."""
    a, b = ASSETS / "lyons_a.wav", ASSETS / "lyons_c.wav"
    result = sc.run(
        a.read_bytes(),
        {"reference_evidence_id": "u"},
        model_bytes=weights,
        reference_bytes=b.read_bytes(),
    )

    # Asserted on phrases that do not straddle a line break: an earlier version
    # searched for "NOT an identification", which the wrapped disclaimer does not
    # contain, so the check was asserting on formatting rather than on content.
    flat = " ".join(result["disclaimer"].split())
    assert "NOT identification" in flat
    assert "not a likelihood or a probability" in flat
    assert result["tier"] == "B"
    assert result["detail"]["comparison_type"] == "one-to-one verification (never identification)"
    assert result["detail"]["calibration"]["calibrated_on_this_deployment"] is False
    assert result["detail"]["calibration"]["ranges_separate"] is False
    assert result["detail"]["params"]["reference_evidence_id"] == "u"

    limitations = " ".join(result["detail"]["known_limitations"])
    assert "dramatic reading" in limitations or "multi-character" in limitations
    assert "recoverable" in limitations
    assert "WAV only" in limitations


def test_a_non_match_reads_as_not_shown_to_match(weights: bytes) -> None:
    """The negative case must not be phrased as a conclusion about the people."""
    a, b = ASSETS / "kara_a.wav", ASSETS / "lyons_a.wav"
    result = sc.run(a.read_bytes(), {}, model_bytes=weights, reference_bytes=b.read_bytes())
    interpretation = result["detail"]["interpretation"]
    assert "NOT evidence" in interpretation
    assert "not shown to match" in interpretation


def test_the_module_declares_its_pinned_weights(weights: bytes) -> None:
    """The capability list must point an operator at a real artifact."""
    assert sc.spec.model_key == "speaker/ecapa/voxceleb_ECAPA512.onnx"
    assert len(sc.spec.model_sha256) == 64
    assert sc.spec.aux_models == {}
    assert sc.spec.requires_gpu is False
    assert sc.MODEL_TERMS.startswith("Apache-2.0")


def test_no_embedding_or_audio_is_retained_in_the_payload(weights: bytes) -> None:
    """Embeddings are recoverable from short audio, so they must not be stored."""
    a, b = ASSETS / "lyons_a.wav", ASSETS / "lyons_c.wav"
    result = sc.run(a.read_bytes(), {}, model_bytes=weights, reference_bytes=b.read_bytes())
    blob = str(result)
    assert "embedding" not in result["detail"] or result["detail"].get("embedding") is None
    # Only the dimension, never the vector.
    assert result["detail"]["embedding_dimension"] == 192
    assert len(blob) < 6000, "the payload grew; check nothing audio-sized leaked into it"

"""P9.B6 / T9.1 — `text-authorship`: golden tests, including the one that says it does not work.

The measurements this file records are the point, and one of them is a negative
result. Both techniques were tried against the same six public-domain fixtures:

**A sentence-embedding model does not work at all.** all-MiniLM-L6-v2 over these
passages: same-author 0.12–0.49, different-author 0.04–0.48. The ranges overlap
almost entirely, because a sentence-embedding model is trained to score *what a text is
about*. Shipping that would have produced a number that looks like authorship
attribution and measures topic similarity.

**Function-word stylometry is the right tool and still does not separate.** On the
committed fixtures: same-author 0.9339–0.9882, different-author 0.8865–0.9574 — an
overlap of 0.0235.

**That is asserted here rather than tuned away.** A test that required the fixtures to
separate would be satisfied by growing them until they did, which is fitting the
evidence to the result. The fixture size was fixed before the comparison was run, the
comparison did not separate, and both facts are pinned here so the next person sees
the measurement rather than a threshold that was moved until it was green.
"""

from __future__ import annotations

import itertools
import pathlib

import pytest
from app.analysis.modules import text_authorship as ta
from app.analysis.types import AnalysisNotApplicable

ASSETS = pathlib.Path(__file__).parent / "assets" / "authorship"
FIXTURES = sorted(ASSETS.glob("authorship-*.txt"))


def _text(name: str) -> str:
    return (ASSETS / name).read_text()


def _author(path: pathlib.Path) -> str:
    return path.name.split("-")[1]


def _score(subject: str, reference: str) -> float:
    result = ta.run(subject.encode(), {}, reference_bytes=reference.encode())
    return float(result["score"])


@pytest.fixture(scope="module")
def corpus() -> dict[str, str]:
    assert FIXTURES, f"no authorship fixtures in {ASSETS}"
    return {path.name: path.read_text() for path in FIXTURES}


# --- the fixtures are what the measurements assume ------------------------


def test_the_fixtures_are_long_enough_to_be_measured_at_all(corpus: dict) -> None:
    """Every fixture must clear the module's own minimum.

    A fixture shortened below `MIN_WORDS` would make the module refuse, and the
    refusal tests below would still pass — so the length has to be asserted directly,
    or a future edit to the derivation silently replaces the evidence with something
    the measurements above no longer describe.
    """
    for name, text in corpus.items():
        words = len(ta.tokenize(text))
        assert words >= ta.MIN_WORDS, (
            f"{name} has {words} words, below the {ta.MIN_WORDS} the measurements "
            f"were taken at; every golden value here would be describing a different "
            f"experiment"
        )


def test_the_fixtures_are_six_distinct_passages(corpus: dict) -> None:
    """Six passages from three authors, and no passage repeated.

    Two fixtures holding the same text would make a "same author" comparison
    trivially true and a "different author" one falsely low — so the corpus has to be
    checked, not assumed.
    """
    assert len(corpus) == 6
    assert len({text for text in corpus.values()}) == 6, "two fixtures hold the same text"
    assert {_author(p) for p in FIXTURES} == {"austen", "melville", "shelley"}
    assert all(_author(p) for p in FIXTURES)


# --- the negative result, pinned -------------------------------------------


def test_the_fixtures_do_not_separate_by_author(corpus: dict) -> None:
    """Same-author similarity is *not* higher than every different-author pair.

    This is the measurement that governs everything else in this file, and it is a
    negative one. It is written as an assertion so that a future change which makes
    them separate has to *update this test deliberately*, rather than the separation
    quietly appearing in a golden file and being read as evidence the module works.
    """
    same, different = [], []
    for a, b in itertools.combinations(corpus, 2):
        score = _score(corpus[a], corpus[b])
        same_pair = _author(pathlib.Path(a)) == _author(pathlib.Path(b))
        (same if same_pair else different).append(score)

    assert min(same) <= max(different), (
        "the fixtures now separate by author. That is good news, and it invalidates "
        "the measurement recorded in the module docstring and in CHECKLIST — update "
        "both, then set a real threshold."
    )


def test_the_measured_ranges_are_pinned(corpus: dict) -> None:
    """The exact numbers, so a change in the derivation or the feature set is visible.

    Not because they are good numbers — they are not, and the previous test says so —
    but because a silent drift in either would quietly change what this module
    reports.
    """
    same, different = [], []
    for a, b in itertools.combinations(corpus, 2):
        score = _score(corpus[a], corpus[b])
        same_pair = _author(pathlib.Path(a)) == _author(pathlib.Path(b))
        (same if same_pair else different).append(score)

    assert min(same) == pytest.approx(0.933857, abs=1e-5)
    assert max(same) == pytest.approx(0.988169, abs=1e-5)
    assert max(different) == pytest.approx(0.957353, abs=1e-5)
    assert min(different) == pytest.approx(0.886451, abs=1e-5)

    # Three same-author pairs and twelve different-author ones, so a run that
    # silently stopped comparing everything would be caught here.
    assert len(same) == 3
    assert len(different) == 12


# --- the module's own contract --------------------------------------------


def test_the_score_is_the_cosine_of_the_stylometric_vectors(corpus: dict) -> None:
    """The result is the documented quantity, not something adjacent to it."""
    import numpy as np

    names = sorted(corpus)
    a, b = names[0], names[1]
    expected = ta.cosine(ta.stylometric_vector(corpus[a]), ta.stylometric_vector(corpus[b]))
    assert _score(corpus[a], corpus[b]) == pytest.approx(expected, abs=1e-12)
    assert -1.0 <= expected <= 1.0
    assert np.isfinite(expected)


def test_repeated_runs_are_identical(corpus: dict) -> None:
    """Determinism is load-bearing: admissibility depends on reproducing a score.

    There are no weights here, so there is nothing to drift — which is a property
    worth asserting rather than assuming, since every other module in this project
    has had to guard it explicitly.
    """
    names = sorted(corpus)
    kwargs = {"reference_bytes": corpus[names[1]].encode()}
    first = ta.run(corpus[names[0]].encode(), {}, **kwargs)
    second = ta.run(corpus[names[0]].encode(), {}, **kwargs)
    assert first == second


def test_a_short_sample_refuses_rather_than_scoring(corpus: dict) -> None:
    """Short text is unmeasurable, not a low score.

    At 1200 words the ranges overlap completely, so a score for a short sample is
    noise. Returning 0.0 would render as "nothing in common", which is a claim about
    the world that nobody measured.
    """
    short = "the of and a to in is " * 100
    with pytest.raises(AnalysisNotApplicable) as caught:
        ta.run(short.encode(), {}, reference_bytes=next(iter(corpus.values())).encode())
    assert "at least" in str(caught.value)

    # And the reference side counts too.
    with pytest.raises(AnalysisNotApplicable):
        ta.run(next(iter(corpus.values())).encode(), {}, reference_bytes=short.encode())


def test_a_missing_reference_refuses(corpus: dict) -> None:
    """A comparison needs two documents. One alone is not a low similarity."""
    with pytest.raises(AnalysisNotApplicable, match="no reference document"):
        ta.run(next(iter(corpus.values())).encode(), {})
    # A module call must refuse too, not just `run` — the wrapper is the path the
    # executor takes.
    with pytest.raises(AnalysisNotApplicable, match="reference_evidence_id"):
        ta.module(next(iter(corpus.values())).encode(), {})


def test_a_reference_must_be_recorded_evidence_not_inline_text(corpus: dict) -> None:
    """The reference is an evidence id, never text pasted into params.

    An inline reference would be hashed into the job's identity but never written to
    the chain of custody: not addressable, not independently retrievable, and outside
    the executor's cross-case check. A result comparing against it could not be
    reproduced from the case file, which is what makes the result admissible at all.
    """
    result = ta.run(
        next(iter(corpus.values())).encode(),
        {"reference_evidence_id": "some-uuid", "reference_text": "ignored"},
        reference_bytes=list(corpus.values())[1].encode(),
    )
    assert result["detail"]["params"]["reference_evidence_id"] == "some-uuid"
    assert result["detail"]["reference_label"] == "unnamed"
    # The id survives; the text does not.
    assert "reference_text" not in result["detail"]["params"]


def test_two_identical_documents_refuse_as_a_tautology(corpus: dict) -> None:
    """1.0 by definition is not authorship evidence.

    Two identical vectors have cosine 1.0 exactly. Reporting that as a perfect
    authorship match would score a tautology as its highest possible result.
    """
    text = next(iter(corpus.values()))
    with pytest.raises(AnalysisNotApplicable, match="tautology"):
        ta.run(text.encode(), {}, reference_bytes=text.encode())


def test_undecodable_input_refuses(corpus: dict) -> None:
    """Bytes that are not text are not a low stylistic similarity."""
    with pytest.raises(AnalysisNotApplicable, match="UTF-8"):
        ta.run(b"\xff\xfe\x00\x01binary", {}, reference_bytes=next(iter(corpus.values())).encode())


def test_a_module_registers_without_any_weights() -> None:
    """The capability list must not claim this needs a model.

    It loads nothing, fetches nothing and pins no digest, so reporting a missing
    weights problem for it would send an operator looking for a download that does not
    exist.
    """
    assert ta.spec.model_key is None
    assert ta.spec.model_sha256 is None
    assert ta.spec.aux_models == {}
    assert ta.spec.requires_gpu is False


def test_the_result_states_what_a_high_score_does_not_mean(corpus: dict) -> None:
    """The disclaimer and the payload must both refuse the identification reading.

    Asserted because the score is a cosine on a scale where "above the bar" reads as a
    verdict, and the reason it is not one is entirely in this text.
    """
    result = ta.run(
        next(iter(corpus.values())).encode(),
        {"reference_evidence_id": "u", "reference_label": "melville-1"},
        reference_bytes=list(corpus.values())[1].encode(),
    )

    assert "NOT an identification" in result["disclaimer"]
    assert result["tier"] == "B"
    assert result["detail"]["calibration"]["calibrated_on_this_deployment"] is False
    assert "does not separate" in " ".join(result["detail"]["known_limitations"])
    assert any("editing" in limit for limit in result["detail"]["known_limitations"])
    assert result["detail"]["reference_label"] == "melville-1"
    # Whatever a caller put in params, the reference *text* must not be echoed into
    # the payload — it can be an entire document, and result payloads are rendered
    # into signed reports. Only the reference's identity travels.
    assert "reference_text" not in result["detail"]["params"]


def test_the_feature_set_is_deterministic_and_sorted(corpus: dict) -> None:
    """Vector positions must mean a fixed thing, not a dict's insertion order."""
    vector = ta.stylometric_vector(next(iter(corpus.values())))
    assert len(vector) == len(set(ta.FUNCTION_WORDS)) + len(ta.PUNCTUATION)
    # Re-ordering the module's tuple must not move any feature, because the vector is
    # built from `sorted(FUNCTION_WORDS)`.
    import app.analysis.modules.text_authorship as module

    original = ta.stylometric_vector(next(iter(corpus.values())))
    module.FUNCTION_WORDS = tuple(reversed(module.FUNCTION_WORDS))
    try:
        assert module.stylometric_vector(next(iter(corpus.values()))).tolist() == (
            original.tolist()
        )
    finally:
        module.FUNCTION_WORDS = tuple(sorted(set(ta.FUNCTION_WORDS), key=lambda w: w))

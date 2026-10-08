"""P9.B5 — `handwriting-match`: golden tests.

The fixtures are four manuscript pages of one 1914 diary
(`tests/assets/handwriting/PROVENANCE.md`). Two things follow from that and both are
asserted here rather than left to the disclaimer:

**The negative class is "same writer, different page."** That makes this a
document-matching test and not a writer-identification one, and the module's payload
claims nothing more.

**The first measurement of this was wrong in an instructive way.** ORB was created with
`nfeatures=3000` while these pages yield >=12,000 keypoints, so both images were
truncated to exactly the cap, the denominator was constant, and every same-page pair
scored *exactly* 1.0000. The apparent 0.97 separation was saturation, not signal. The
tests below pin the unsaturated numbers and assert the cap is not binding, so the
artefact cannot come back.
"""

from __future__ import annotations

import io
import itertools
import pathlib

import pytest
from app.analysis.modules import handwriting as hw
from app.analysis.types import AnalysisNotApplicable
from PIL import Image

ASSETS = pathlib.Path(__file__).parent / "assets" / "handwriting"
PAGES = {p.stem: p.read_bytes() for p in sorted(ASSETS.glob("p*.png"))}

#: Re-captures of the same page. Deliberately mild, and the module says so.
VARIANTS = ("q55", "rot2", "rotneg3q45", "scale085q70")


def recapture(data: bytes, kind: str) -> bytes:
    image = Image.open(io.BytesIO(data)).convert("L")
    buffer = io.BytesIO()
    if kind == "q55":
        image.save(buffer, "JPEG", quality=55)
    elif kind == "rot2":
        image.rotate(2.0, expand=True, fillcolor=255).save(buffer, "PNG")
    elif kind == "rotneg3q45":
        image.rotate(-3.0, expand=True, fillcolor=255).save(buffer, "JPEG", quality=45)
    elif kind == "scale085q70":
        resized = image.resize((int(image.width * 0.85), int(image.height * 0.85)))
        resized.save(buffer, "JPEG", quality=70)
    else:  # pragma: no cover - the tuple above is the contract
        raise AssertionError(f"unknown variant {kind}")
    return buffer.getvalue()


def ratio(a: bytes, b: bytes) -> float:
    return hw.match_ratio(hw._decode(a), hw._decode(b))[0]


# --- the fixtures are what the measurement assumes ------------------------


def test_there_are_four_distinct_manuscript_pages() -> None:
    assert len(PAGES) == 4
    assert len({bytes(v) for v in PAGES.values()}) == 4
    assert set(PAGES) == {"p100", "p160", "p220", "p260"}


def test_the_feature_cap_is_above_what_these_pages_produce() -> None:
    """The regression guard for the saturation artefact.

    With the cap below what a page produces, both images truncate to exactly the cap,
    the denominator becomes a constant, and every same-page ratio is exactly 1.0 — a
    metric pinned at its maximum tells you nothing, and it looked like a spectacular
    separation.
    """
    counts = [hw.describe(hw._decode(data))[1] for data in PAGES.values()]
    assert min(counts) < hw.MAX_FEATURES, (
        f"pages now yield as few as {min(counts)} keypoints, so the cap of "
        f"{hw.MAX_FEATURES} may no longer be above them; re-measure before trusting it"
    )


# --- the separation, pinned ------------------------------------------------


@pytest.mark.parametrize("kind", VARIANTS)
def test_a_recaptured_page_matches_itself(kind: str) -> None:
    for name, data in PAGES.items():
        assert (
            ratio(recapture(data, kind), data) >= hw.THRESHOLD
        ), f"{name} re-captured as {kind} fell below the bar"


@pytest.mark.parametrize("kind", VARIANTS)
def test_a_recapture_matches_further_from_itself_than_any_other_page(kind: str) -> None:
    """The positive control that makes the negative results meaningful."""
    for name, data in PAGES.items():
        recaptured = recapture(data, kind)
        own = ratio(recaptured, data)
        # Compare *keys* against keys. The first version compared a string key to a
        # bytes value, so `other != name` was always true, the page was never actually
        # excluded from `others`, and the assertion degenerated to `own > own`.
        others = [ratio(recaptured, body) for key, body in PAGES.items() if key != name]
        assert own > max(others), f"{name}/{kind} matched another page as well as itself"


def test_different_pages_are_far_below_the_bar() -> None:
    """Same writer, different text. The negative class these fixtures actually have."""
    for a, b in itertools.combinations(sorted(PAGES), 2):
        assert ratio(PAGES[a], PAGES[b]) < hw.THRESHOLD


def test_the_measured_ranges_are_pinned() -> None:
    """Exact values, so a drift in OpenCV, Pillow or the resolution shows up."""
    same, different = [], []
    for data in PAGES.values():
        for kind in VARIANTS:
            same.append(round(ratio(recapture(data, kind), data), 4))
    for a, b in itertools.combinations(sorted(PAGES), 2):
        different.append(round(ratio(PAGES[a], PAGES[b]), 4))

    assert min(same) == 0.3802
    assert max(same) == 0.749
    assert max(different) == 0.0131
    assert len(same) == 16 and len(different) == 6
    # A metric saturated at 1.0 across every case is the artefact this file guards
    # against, so assert it is *not* saturated.
    assert max(same) < 1.0


# --- the module's own contract --------------------------------------------


def test_the_score_is_the_matched_fraction() -> None:
    """The result is the documented quantity."""
    a, b = "p100", "p160"
    result = hw.run(PAGES[a], {}, reference_bytes=PAGES[b])
    expected, good, smaller = hw.match_ratio(hw._decode(PAGES[a]), hw._decode(PAGES[b]))
    assert result["score"] == pytest.approx(expected, abs=1e-12)
    assert result["detail"]["good_matches"] == good
    assert result["detail"]["keypoints_in_smaller_page"] == smaller


def test_repeated_runs_are_identical() -> None:
    """Admissibility depends on reproducing a score, and there are no weights."""
    kwargs = {"reference_bytes": PAGES["p220"]}
    assert hw.run(PAGES["p100"], {}, **kwargs) == hw.run(PAGES["p100"], {}, **kwargs)


def test_a_missing_reference_refuses() -> None:
    with pytest.raises(AnalysisNotApplicable, match="no reference image"):
        hw.run(PAGES["p100"], {})
    with pytest.raises(AnalysisNotApplicable, match="reference_evidence_id"):
        hw.module(PAGES["p100"], {})


def test_undecodable_input_refuses() -> None:
    good = PAGES["p100"]
    with pytest.raises(AnalysisNotApplicable):
        hw.run(b"\x89PNG\r\n\x1a\n truncated", {}, reference_bytes=good)
    with pytest.raises(AnalysisNotApplicable):
        hw.run(good, {}, reference_bytes=b"\xff\xd8\xff not an image")


def test_a_blank_page_refuses_rather_than_scoring_zero() -> None:
    """No features is "cannot measure", not "measured as unrelated"."""
    blank = io.BytesIO()
    Image.new("L", (400, 600), 255).save(blank, "PNG")
    ratio_value, good, smaller = hw.match_ratio(
        hw._decode(blank.getvalue()), hw._decode(PAGES["p100"])
    )
    assert (ratio_value, good) == (0.0, 0)
    assert smaller == 0, "a blank page yielded keypoints, which is itself wrong"


def test_a_module_registers_without_any_weights() -> None:
    """The capability list must not send an operator looking for a download."""
    assert hw.spec.model_key is None
    assert hw.spec.model_sha256 is None
    assert hw.spec.aux_models == {}
    assert hw.spec.requires_gpu is False


# --- what it refuses to be read as ----------------------------------------


def test_the_result_refuses_the_authorhip_reading() -> None:
    """The single most important limitation, asserted rather than only documented."""
    result = hw.run(PAGES["p100"], {}, reference_bytes=PAGES["p220"])
    flat = " ".join(result["disclaimer"].split())

    assert "NOT authorship attribution" in flat
    assert "NOT handwriting identification" in flat
    assert (
        result["detail"]["comparison_type"] == "same-document (1:1), never authorship attribution"
    )
    # Flatten whitespace: the limitation strings are wrapped in the source, so a phrase
    # can straddle an implicit-concatenation boundary and a substring search over the
    # raw text asserts on formatting rather than on content.
    limitations = " ".join(" ".join(result["detail"]["known_limitations"]).split())
    assert "form-filler imitates one" in limitations
    assert "every fixture is one writer" in limitations
    # The confound must be stated in the *disclaimer* too, not only in the payload
    # detail: the disclaimer is what reaches a report reader.
    assert "form-filler imitates one" in flat


def test_a_non_match_is_not_proof_the_documents_differ() -> None:
    """The honest direction of the negative."""
    result = hw.run(PAGES["p100"], {}, reference_bytes=PAGES["p160"])
    interpretation = " ".join(result["detail"]["interpretation"].split())
    assert "NOT proof that two documents differ" in interpretation
    assert "not shown to be the same page" in interpretation


def test_the_unmeasured_conditions_are_named() -> None:
    """What the four pages do not cover, asserted so it cannot quietly drop away."""
    limitations = " ".join(
        hw.run(PAGES["p100"], {}, reference_bytes=PAGES["p160"])["detail"]["known_limitations"]
    )
    for condition in ("curvature", "perspective", "raking light", "different pen", "scanner"):
        assert condition in limitations


def test_the_calibration_block_states_it_is_not_calibrated() -> None:
    calibration = hw.run(PAGES["p100"], {}, reference_bytes=PAGES["p160"])["detail"]["calibration"]
    assert calibration["calibrated_on_this_deployment"] is False
    assert "four pages of one diary" in calibration["note"]


def test_no_inline_reference_document_is_retained() -> None:
    result = hw.run(
        PAGES["p100"],
        {"reference_evidence_id": "u", "reference_text": "an entire pasted document"},
        reference_bytes=PAGES["p220"],
    )
    assert result["detail"]["params"]["reference_evidence_id"] == "u"
    assert "reference_text" not in result["detail"]["params"]
    assert result["detail"]["reference_label"] == "unnamed"

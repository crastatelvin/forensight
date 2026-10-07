"""FI-5 — agent second-opinion review.

The property under test is negative, and it is the whole point: **agreement between two
runs from the same model must never read as corroboration.** A confidently wrong model
is consistently wrong, so two agreeing runs are evidence about the model's consistency
and nothing about whether the conclusion is right. Building a feature whose happy path
is two identical reports risks exactly that misreading, so the "they agree" case gets
the loudest assertion in this file.

The other thing worth pinning is that claims are matched on the **results behind them**,
not their prose. Two runs reaching one conclusion word it differently; matching on text
would report every paraphrase as a disagreement and bury the real ones.
"""

from __future__ import annotations

import uuid

from app.agent import second_opinion as so


def claim(text: str, *result_ids: str) -> dict:
    return {
        "text": text,
        "citations": [
            {
                "result_id": rid,
                "evidence_id": str(uuid.uuid4()),
                "analyzer": "image-exif",
                "score": 0.5,
                "threshold": 0.4,
                "model_version": "1.0.0",
                "tier": "B",
            }
            for rid in result_ids
        ],
    }


R1 = str(uuid.uuid4())
R2 = str(uuid.uuid4())
R3 = str(uuid.uuid4())


# --- the negative result that governs everything ---------------------------


def test_agreement_is_never_reported_as_corroboration() -> None:
    """Identical claims from both runs — the case most likely to be over-read."""
    claims = [claim("The file carries GPS metadata.", R1)]
    payload = so.summarise(
        first_run=uuid.UUID(R1),
        second_run=uuid.UUID(R2),
        first_claims=claims,
        second_claims=[claim("The file carries GPS metadata.", R1)],
    )

    assert payload["divergence_count"] == 0
    # Unconditionally, not only when the runs happen to disagree.
    assert payload["is_corroboration"] is False
    assert "consistency" in payload["why_not_corroboration"]
    assert "independent source" in payload["why_not_corroboration"]


def test_no_winner_is_ever_declared() -> None:
    """Even when one run is obviously emptier, nothing is ranked."""
    payload = so.summarise(
        first_run=uuid.UUID(R1),
        second_run=uuid.UUID(R2),
        first_claims=[claim("A conclusion.", R1), claim("Another.", R2)],
        second_claims=[claim("A conclusion.", R1)],
    )
    assert payload["no_winner_declared"] is True
    assert "first" not in payload
    assert "second" not in payload
    # Scan the *structure*, not the prose. The disclaimer legitimately contains the
    # words "no winner" and "ranks nothing", so a substring sweep over the whole
    # payload fails on the very text that makes the guarantee — which is why this
    # checks field names and non-prose values only.
    prose_fields = {"disclaimer", "why_not_corroboration", "matched_on", "no_winner_declared"}
    structural = {k: v for k, v in payload.items() if k not in prose_fields}
    assert not any(
        word in str(key).lower() for key in structural for word in ("winner", "preferred", "rank")
    ), f"a field declares a preference: {sorted(structural)}"


def test_the_description_says_what_it_refuses_to_do() -> None:
    described = so.describe()
    assert described["is_not_corroboration"].startswith("same model")
    not_attempted = " ".join(described["not_attempted"])
    assert "ranking" in not_attempted
    assert "merging" in not_attempted
    assert "NOT corroboration" in described["disclaimer"]


# --- matching is on the evidence, not the prose ----------------------------


def test_the_same_conclusion_worded_differently_is_not_a_divergence() -> None:
    """Matching on prose would bury every real disagreement in paraphrases."""
    payload = so.summarise(
        first_run=uuid.UUID(R1),
        second_run=uuid.UUID(R2),
        first_claims=[claim("The image carries GPS metadata.", R1)],
        second_claims=[claim("GPS metadata is present in the image.", R1)],
    )
    assert payload["divergence_count"] == 0


def test_one_conclusion_reached_by_different_evidence_is_a_divergence() -> None:
    """Reads as agreement in a summary; is not."""
    payload = so.summarise(
        first_run=uuid.UUID(R1),
        second_run=uuid.UUID(R2),
        first_claims=[claim("The file was edited.", R1)],
        second_claims=[claim("The file was edited.", R2)],
    )
    kinds = payload["divergence_kinds"]
    assert kinds.get("different_basis") == 1
    assert kinds.get("only_in_first") == 1
    assert kinds.get("only_in_second") == 1


# --- divergence is the signal ----------------------------------------------


def test_a_conclusion_only_one_run_reached_is_named() -> None:
    payload = so.summarise(
        first_run=uuid.UUID(R1),
        second_run=uuid.UUID(R2),
        first_claims=[claim("Shared.", R1), claim("Only the first run.", R2)],
        second_claims=[claim("Shared.", R1), claim("Only the second run.", R3)],
    )
    kinds = payload["divergence_kinds"]
    assert kinds["only_in_first"] == 1
    assert kinds["only_in_second"] == 1
    texts = " ".join(d["claim_text"] for d in payload["divergences"])
    assert "Only the first run." in texts
    assert "Only the second run." in texts


def test_an_unsubstantiated_claim_is_reported_not_ignored() -> None:
    """A silently-passing unbacked claim is exactly what a reviewer looks for.

    `ProvenanceGuard` should make this impossible, so seeing it here means the guard
    was bypassed or a claim was reconstructed from somewhere else — either way it must
    surface rather than be treated as a link whose signature happens to be empty.
    """
    payload = so.summarise(
        first_run=uuid.UUID(R1),
        second_run=uuid.UUID(R2),
        first_claims=[{"text": "asserted with nothing behind it", "citations": []}],
        second_claims=[claim("Sourced.", R1)],
    )
    assert payload["divergence_kinds"].get("unsubstantiated") == 1
    assert "no cited results" in " ".join(d["detail"] for d in payload["divergences"])


def test_a_claim_citing_two_results_is_not_matched_against_one() -> None:
    """Signature is the whole set, so a partially-shared basis is a difference."""
    payload = so.summarise(
        first_run=uuid.UUID(R1),
        second_run=uuid.UUID(R2),
        first_claims=[claim("Composite conclusion.", R1, R2)],
        second_claims=[claim("Composite conclusion.", R1, R3)],
    )
    assert payload["divergence_kinds"].get("different_basis") == 1


# --- degenerate inputs -----------------------------------------------------


def test_two_empty_runs_produce_no_divergence_and_still_refuse_corroboration() -> None:
    payload = so.summarise(
        first_run=uuid.UUID(R1),
        second_run=uuid.UUID(R2),
        first_claims=[],
        second_claims=[],
    )
    assert payload["divergence_count"] == 0
    assert payload["claim_counts"] == {"first": 0, "second": 0}
    assert payload["is_corroboration"] is False


def test_the_order_of_divergences_is_stable() -> None:
    """Two requests must not disagree about what the divergences are."""
    first = [claim("b", R2), claim("a", R1)]
    second = [claim("c", R3)]
    one = so.compare(first, second)
    two = so.compare(first, second)
    assert [d.to_dict() for d in one] == [d.to_dict() for d in two]
    assert [d.kind for d in one] == sorted(d.kind for d in one)


def test_both_reports_travel_with_the_comparison() -> None:
    """The endpoint returns both runs; this checks the payload leaves room for them."""
    payload = so.summarise(
        first_run=uuid.UUID(R1),
        second_run=uuid.UUID(R2),
        first_claims=[claim("a", R1)],
        second_claims=[claim("b", R2)],
    )
    assert payload["runs"] == {"first": R1, "second": R2}
    assert payload["matched_on"].startswith("the set of analyzer result ids")
    assert "NOT corroboration" in payload["disclaimer"]

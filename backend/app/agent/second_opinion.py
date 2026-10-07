"""Second-opinion review mode (FI-5).

**A second opinion from the same model is not corroboration, and this module is built
so that cannot be misread.** `resolve_model` picks one provider; a second run through it
is a second *sample* from the same distribution, not a second witness. If both runs
agree, that is evidence about the model's consistency and **not** evidence that the
conclusion is right — a confidently wrong model is consistently wrong, and agreement is
what that looks like. Presenting two agreeing runs as corroboration would be the single
most misleading thing this system could do with its own output.

So the output of this module is **divergence**, not a verdict. What is genuinely
useful about running an investigation twice is where the two runs *disagree*: a claim
present in one and absent in the other is either a gap in the first run or noise in the
second, and either way it is exactly where a human should look.

**No run is declared the winner.** There is no scoring, no ranking and no "preferred"
report. The findings are returned side by side with the divergences named, because a
tool that picks for the examiner removes the judgement that made them the examiner. If
one run is better, that is a conclusion drawn from the divergences by someone who can
read the case.

**Claims are matched on their cited results, not their prose.** Two runs reaching the
same conclusion will word it differently; what identifies the conclusion is the set of
analyzer results behind it. Matching on text would report every paraphrase as a
disagreement and bury the real ones.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

DISCLAIMER = (
    "Second-opinion review — two agent runs over the same case objective, reported side "
    "by side with their disagreements named. This is NOT corroboration and NOT a "
    "verdict. Both runs come from the same model through the same provider, so "
    "agreement between them is evidence about the model's consistency and nothing "
    "about whether either conclusion is right: a confidently wrong model is "
    "consistently wrong. Independent corroboration needs an independent source — a "
    "different model, a different provider, or a human who worked the case "
    "independently. Divergence is where a human should look; this module names no "
    "winner and ranks nothing."
)


#: How claims are matched across runs.
#:
#: By cited result ids, not by text. Two runs reaching one conclusion will phrase it
#: differently, and the citations are what identify the conclusion. Matching on prose
#: would report every paraphrase as a disagreement and bury the real ones.
def claim_signature(claim: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        str(citation.get("result_id", ""))
        for citation in claim.get("citations", [])
        if citation.get("result_id")
    )


@dataclass(frozen=True)
class Divergence:
    """One kind of disagreement between two runs."""

    kind: str
    detail: str
    #: Claim text from the first run, when the divergence is about a specific claim.
    claim_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail, "claim_text": self.claim_text}


def compare(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[Divergence]:
    """Where two runs' claims differ, by what each one rests on.

    Three kinds, and the third is the one that matters most:

    - `only_in_first` / `only_in_second`: a conclusion one run reached and the other
      did not. This is the signal.
    - `different_basis`: both runs made a claim, but resting on different analyzer
      results — the same conclusion reached by different evidence, which reads as
      agreement in a summary and is not.
    - `unsubstantiated`: a claim in either run with **no citations at all**. This
      should be impossible — `ProvenanceGuard` refuses it — so it appearing here means
      the guard was bypassed or the claim was reconstructed from somewhere else. It is
      reported rather than ignored because a silently-passing unbacked claim in a
      second-opinion report is precisely the thing a reviewer is looking for.
    """
    by_signature_first: dict[frozenset[str], dict[str, Any]] = {}
    by_signature_second: dict[frozenset[str], dict[str, Any]] = {}
    for claim in first:
        by_signature_first.setdefault(claim_signature(claim), claim)
    for claim in second:
        by_signature_second.setdefault(claim_signature(claim), claim)

    divergences: list[Divergence] = []

    for signature, claim in by_signature_first.items():
        if not signature:
            divergences.append(
                Divergence(
                    kind="unsubstantiated",
                    detail="the first run carries a claim with no cited results",
                    claim_text=str(claim.get("text", ""))[:200],
                )
            )
        elif signature not in by_signature_second:
            divergences.append(
                Divergence(
                    kind="only_in_first",
                    detail=(
                        "this run reached a conclusion the second did not, resting on "
                        f"{len(signature)} result(s)"
                    ),
                    claim_text=str(claim.get("text", ""))[:200],
                )
            )

    for signature, claim in by_signature_second.items():
        if not signature:
            divergences.append(
                Divergence(
                    kind="unsubstantiated",
                    detail="the second run carries a claim with no cited results",
                    claim_text=str(claim.get("text", ""))[:200],
                )
            )
        elif signature not in by_signature_first:
            divergences.append(
                Divergence(
                    kind="only_in_second",
                    detail=(
                        "the second run reached a conclusion the first did not, "
                        f"resting on {len(signature)} result(s)"
                    ),
                    claim_text=str(claim.get("text", ""))[:200],
                )
            )

    # `different_basis` is computed from claim *text*, which is the one place prose
    # comparison is right: the question is whether two runs said the same thing while
    # citing different evidence, and matching on text is how you notice that.
    first_by_text = {str(c.get("text", "")).strip(): c for c in first}
    second_by_text = {str(c.get("text", "")).strip(): c for c in second}
    for text, claim in first_by_text.items():
        other = second_by_text.get(text)
        if other is None or not text:
            continue
        if claim_signature(claim) != claim_signature(other):
            divergences.append(
                Divergence(
                    kind="different_basis",
                    detail="both runs made this claim, resting on different results",
                    claim_text=text[:200],
                )
            )

    return sorted(divergences, key=lambda d: (d.kind, d.claim_text))


def summarise(
    *,
    first_run: uuid.UUID,
    second_run: uuid.UUID,
    first_claims: list[dict[str, Any]],
    second_claims: list[dict[str, Any]],
) -> dict[str, Any]:
    """The full second-opinion payload.

    Both reports are included alongside the divergences rather than instead of them: the
    point is for a reviewer to read both and see where they part company, and returning
    only the differences would hide the reasoning behind each.
    """
    divergences = compare(first_claims, second_claims)
    kinds: dict[str, int] = {}
    for divergence in divergences:
        kinds[divergence.kind] = kinds.get(divergence.kind, 0) + 1

    return {
        "runs": {
            "first": str(first_run),
            "second": str(second_run),
        },
        "claim_counts": {"first": len(first_claims), "second": len(second_claims)},
        "divergences": [d.to_dict() for d in divergences],
        "divergence_count": len(divergences),
        "divergence_kinds": kinds,
        # Stated unconditionally rather than only when the runs happen to disagree.
        # Agreement is the case most likely to be over-read, so it is the case the
        # warning has to survive.
        "is_corroboration": False,
        "why_not_corroboration": (
            "both runs used the same model through the same provider; agreement is "
            "evidence about consistency, not about correctness. Independent "
            "corroboration needs an independent source."
        ),
        "no_winner_declared": True,
        "matched_on": "the set of analyzer result ids behind each claim, not its prose",
        "disclaimer": DISCLAIMER,
    }


def describe() -> dict[str, Any]:
    """What this mode is and is not, served rather than duplicated into a client."""
    return {
        "purpose": (
            "surface where two runs over the same objective disagree, so a reviewer "
            "knows where to look"
        ),
        "is_not_corroboration": (
            "same model, same provider; agreement says nothing about correctness"
        ),
        "not_attempted": [
            "ranking the two runs",
            "picking a preferred report",
            "scoring which run is more reliable",
            "merging the two runs into one answer",
        ],
        "disclaimer": DISCLAIMER,
    }

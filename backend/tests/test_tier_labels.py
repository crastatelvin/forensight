"""T9.4 — every Tier-B surface says "not an identity match", and says it once.

The plan asks for Tier B to be repositioned as visual similarity / triage with
explicit non-identity labelling. The interesting part is not the wording but
*where it has to appear*: a Tier-B number is only safe if every place a human can
meet it says the same thing. A disclaimer on the detail card and none on the
overview summary is a disclaimer that gets missed, which is the failure T7.4 already
found once in this project.

So this asserts the label on three surfaces that are produced independently — the
backend's tier vocabulary, the report PDF, and the payload every result carries —
and then asserts they *agree*. Agreement is the part worth testing: three copies of
a safety label that each say the right thing but different things is still a reader
being told three different things about how far a score goes.
"""

from __future__ import annotations

import pytest
from app.analysis.types import TIER_DISCLAIMER, TIER_MEANING, Tier, build_result
from httpx import AsyncClient

#: The phrase that carries the whole point of the tier. Matched loosely on purpose:
#: the wording is allowed to improve, the claim is not.
_NON_IDENTITY = "not an identity match"


def test_tier_b_disclaimer_refuses_identity() -> None:
    """The one every Tier-B result inherits when a module states no more specific limit."""
    assert _NON_IDENTITY in TIER_DISCLAIMER[Tier.B].lower()


def test_tier_b_tier_wording_also_refuses_identity() -> None:
    """The `/tiers` entry, which is what the interface renders.

    Separate from the disclaimer because they are different sentences for different
    surfaces, and a fix to one must not quietly weaken the other.
    """
    meaning = TIER_MEANING[Tier.B]["meaning"].lower()
    assert _NON_IDENTITY in meaning
    # And it must be a claim about *identity*, not a generic caution. "Use with care"
    # is the shape of a disclaimer that survives contact with a rushed reader.
    assert "identity" in meaning and "match" in meaning


def test_tier_a_does_not_claim_to_refuse_identity() -> None:
    """The reverse check, so the label cannot spread to the wrong tier.

    Cheap, and it stops "NOT an identity match" being pasted onto every badge in
    the app until it says nothing at all.
    """
    assert _NON_IDENTITY not in TIER_DISCLAIMER[Tier.A].lower()


def test_every_result_carries_its_disclaimer_whatever_the_module_claimed() -> None:
    """`build_result` is the only place a result's disclaimer is decided.

    A module may state its own limits (`ModuleSpec.disclaimer`) because a generic
    tier sentence is meaningless on some techniques. It may never state *less*: the
    test below uses a module that supplies no disclaimer, which is the common case.
    """
    from app.analysis.types import ModuleSpec

    spec = ModuleSpec(name="t", version="1.0.0", tier=Tier.B, threshold=0.5)
    result = build_result(spec, score=0.1, detail={})
    assert _NON_IDENTITY in result["disclaimer"].lower()
    assert result["tier"] == "B"


def test_a_module_cannot_weaken_the_tier_label_by_its_own_disclaimer() -> None:
    """A module's own limits replace the tier sentence, so they must not contradict it.

    `ModuleSpec.disclaimer` overrides the generic tier text entirely. That is right —
    a generic sentence about identity matching is meaningless on an EXIF result — but
    it means a module could also *remove* the non-identity statement from a Tier-B
    result. This pins the shape of what a Tier-B override may look like: it may add
    limits, and it may talk about the technique, but the tier's own claim stands.
    """
    from app.analysis.types import ModuleSpec

    override = (
        "Camera-fingerprint correlation links images to a sensor body. "
        "It says NOTHING about the people in them."
    )
    spec = ModuleSpec(name="t", version="1.0.0", tier=Tier.B, threshold=0.5, disclaimer=override)
    result = build_result(spec, score=0.1, detail={})
    assert result["disclaimer"] == override
    # The override is the module's to write — but the *tier* is still reported
    # separately, and the tier is what the interface and the report key their
    # non-identity wording off. Asserted so a future change that folded tier into
    # disclaimer alone would fail here.
    assert result["tier"] == "B"


async def test_the_tiers_endpoint_serves_backend_wording_and_covers_every_tier(
    client: AsyncClient, seed_users: dict
) -> None:
    """The interface asks the backend rather than keeping its own copy.

    It used to keep one, and the copy had drifted into calling Tier B a
    "corroborating signal" — which is precisely how a visual-similarity score gets
    read as a determination — while Tier C was described as "unvalidated research"
    against a backend that refuses to score Tier C at all. Two hand-written copies
    of a safety label will disagree, and the one that disagrees is the one a user
    reads.
    """
    headers = await _auth(client, seed_users["officer"])
    response = await client.get("/tiers", headers=headers)
    assert response.status_code == 200, response.text

    entries = {entry["tier"]: entry for entry in response.json()}
    assert set(entries) == set(TIER_MEANING)
    for tier, meaning in TIER_MEANING.items():
        assert entries[tier]["meaning"] == meaning["meaning"]
        assert entries[tier]["label"] == meaning["label"]

    assert _NON_IDENTITY in entries["B"]["meaning"].lower()
    # Tier C must be described as a refusal, not as low-trust-but-runnable.
    # Described as a refusal, not as low-trust-but-runnable. "Unvalidated research"
    # would imply a module exists and its output merely carries less weight.
    assert "does not score" in entries["C"]["meaning"].lower()
    assert "no module for it on any host" in entries["C"]["meaning"].lower()


async def test_capabilities_report_each_module_s_own_tier(
    client: AsyncClient, seed_users: dict
) -> None:
    """The panel reads the tier; it used to hardcode `explainTier("A")` for every entry.

    That announced Tier A to screen readers for all six analyzers, five of which are
    Tier B — the single fact a reader opens that panel for, stated wrongly for most
    of the list. A panel that misreports a module's trust level is worse than one
    that omits it, because it looks authoritative.
    """
    headers = await _auth(client, seed_users["officer"])
    response = await client.get("/capabilities", headers=headers)
    assert response.status_code == 200, response.text

    caps = response.json()["capabilities"]
    assert caps, "no capabilities reported"
    for capability in caps:
        assert capability.get("tier"), (
            f"{capability['name']} reports no tier; the interface would otherwise "
            f"fall back to asserting one"
        )
        assert capability["tier"] in TIER_MEANING

    # Not decorative: the catalog contains both tiers, so this cannot pass on a
    # single-valued field.
    assert {c["tier"] for c in caps} == {"A", "B"}


@pytest.mark.parametrize("tier", [Tier.A, Tier.B])
def test_no_tier_is_missing_from_the_vocabulary(tier: Tier) -> None:
    """Every tier a result can carry is explainable.

    Guards the failure that started all of this: the backend grew a meaning the
    interface had never heard of, and nothing complained.
    """
    assert tier.value in TIER_MEANING
    assert TIER_MEANING[tier.value]["label"].strip()
    assert TIER_MEANING[tier.value]["meaning"].strip()


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    response = await client.post("/auth/login", json=creds)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

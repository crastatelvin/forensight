"""T9.5 — Tier C has no scoring path, and that is asserted rather than assumed.

The property under test is negative, which is why it needs a test at all: nothing
here proves Tier C works, because Tier C's job is to *not work*, loudly. A catalog
entry that quietly became scoreable would look exactly like every other feature
this project has added.

Four things are pinned:

1. **No job row, no worker message, no result.** The refusal happens in the router
   before the job is created. If a Tier-C attempt left a row behind it would appear
   in the case's analysis listing and audit log looking like real work.
2. **The refusal is specific.** "unsupported by design" plus what the discipline is
   and what to do instead. A bare 422 with no explanation reads as a broken system,
   and an examiner who has just uploaded a blood sample deserves better.
3. **It is not `unsupported`.** That status means *this host* cannot run a real
   module and provisioning would fix it. Tier C means no host ever can, so reporting
   it that way would promise a fix that does not exist.
4. **The catalog is complete and honest.** Every entry names a discipline, says why
   there is no score, and says what to do instead. An entry without an alternative
   is a dead end for the person reading it.
"""

from __future__ import annotations

import uuid

import pytest
from app.analysis import lab_only
from app.analysis.models import AnalysisJob, AnalysisResult
from app.analysis.types import JobStatus
from app.auth.models import User
from app.domain.models import Case, Evidence
from httpx import AsyncClient
from sqlalchemy import func, select

#: The disciplines Phase 9 names. Listed explicitly rather than derived from the
#: catalog: a test that reads its expectation from the thing it is checking asserts
#: only that the code is self-consistent.
REQUIRED_TECHNIQUES = {
    "dna-profiling",
    "toxicology",
    "pollen",
    "insect-identification",
    "ignitable-liquid-residue",
    "explosive-residue",
    "odor-profile",
}


def test_the_catalog_covers_every_discipline_phase_9_names() -> None:
    assert set(lab_only.BY_NAME) >= REQUIRED_TECHNIQUES


def test_every_technique_explains_itself_and_offers_an_alternative() -> None:
    """Each entry is written to be *shown to an examiner*.

    Someone who has just been told "no" reads `why` and `instead`. An entry with an
    empty field, or one that says only that the feature is unavailable, leaves them
    with nothing to act on — which is indistinguishable from the platform being
    broken.
    """
    for technique in lab_only.TECHNIQUES:
        assert technique.discipline.strip(), f"{technique.name} has no discipline"
        assert technique.why.strip(), f"{technique.name} says nothing about why"
        assert technique.instead.strip(), f"{technique.name} offers no alternative"

        # The wording must not read as temporary or forgotten. "Not currently
        # supported" and "coming soon" are promises, and this platform makes none:
        # there is no module on any host and provisioning nothing will change that.
        lowered = technique.why.lower()
        for temporary in ("not implemented", "coming soon", "todo", "for now", "currently"):
            assert temporary not in lowered, (
                f"{technique.name} reads as temporary ({temporary!r}); Tier C is a "
                f"permanent decision, not a gap awaiting work"
            )


async def test_a_tier_c_request_is_refused_and_names_the_reason(
    client: AsyncClient,
    sessionmaker_fixture: object,
    seed_users: dict,
) -> None:
    """The refusal says what this is and what to do instead.

    Asserted on the prefix so the wording can improve without breaking, and on the
    two substantive halves so it cannot be shortened into uselessness.
    """
    headers = await _auth(client, seed_users["officer"])

    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        user = await session.get(User, uuid.UUID(seed_users["officer"]["id"]))
        case = Case(number="LAB-1", title="lab", owner_id=user.id)
        session.add(case)
        await session.flush()
        evidence = Evidence(
            case_id=case.id,
            filename="sample.pdf",
            sha256="a" * 64,
            content_type="application/pdf",
            size_bytes=10,
            storage_key="k",
            uploaded_by=user.id,
        )
        session.add(evidence)
        await session.commit()
        case_id, evidence_id = case.id, evidence.id

    response = await client.post(
        f"/cases/{case_id}/analyses",
        json={"evidence_id": str(evidence_id), "module": "dna-profiling"},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail.startswith(lab_only.REFUSAL_PREFIX), detail
    assert "Tier-C" in detail
    # The two halves that make the refusal actionable.
    assert "DNA profiling" in detail
    assert "Instead:" in detail

    # And nothing was created. This is the load-bearing assertion: a job row left
    # behind would appear in the case's analysis listing looking like real work,
    # and the case would carry a record of an analysis that never happened.
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        jobs = (
            await session.execute(
                select(func.count()).select_from(AnalysisJob).where(AnalysisJob.case_id == case_id)
            )
        ).scalar_one()
        results = (
            await session.execute(
                select(func.count())
                .select_from(AnalysisResult)
                .where(AnalysisResult.case_id == case_id)
            )
        ).scalar_one()
    assert jobs == 0, "a Tier-C refusal left a job row behind"
    assert results == 0, "a Tier-C refusal left a result row behind"


async def test_every_tier_c_technique_is_refused_the_same_way(
    client: AsyncClient,
    sessionmaker_fixture: object,
    seed_users: dict,
) -> None:
    """One refusal, applied to all of them.

    Parameterised over the whole catalog rather than spot-checking DNA, because the
    failure this guards against is a technique being quietly forgotten — and a
    forgotten technique is the one an examiner stumbles on.
    """
    headers = await _auth(client, seed_users["officer"])
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        user = await session.get(User, uuid.UUID(seed_users["officer"]["id"]))
        case = Case(number="LAB-ALL", title="lab", owner_id=user.id)
        session.add(case)
        await session.flush()
        evidence = Evidence(
            case_id=case.id,
            filename="sample.pdf",
            sha256="b" * 64,
            content_type="application/pdf",
            size_bytes=10,
            storage_key="k",
            uploaded_by=user.id,
        )
        session.add(evidence)
        await session.commit()
        case_id, evidence_id = case.id, evidence.id

    for name in sorted(lab_only.BY_NAME):
        response = await client.post(
            f"/cases/{case_id}/analyses",
            json={"evidence_id": str(evidence_id), "module": name},
            headers=headers,
        )
        assert response.status_code == 422, f"{name} was not refused: {response.text}"
        assert response.json()["detail"].startswith(lab_only.REFUSAL_PREFIX), name


async def test_the_refusal_is_audited(
    client: AsyncClient,
    sessionmaker_fixture: object,
    seed_users: dict,
) -> None:
    """A refusal is a decision somebody made, so it belongs in the audit chain.

    Nobody scored a DNA profile — but somebody asked for one and was told no. That
    is exactly the kind of event an audit log exists to record, and it is the only
    trace left once no job row is written.
    """
    headers = await _auth(client, seed_users["officer"])
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        user = await session.get(User, uuid.UUID(seed_users["officer"]["id"]))
        case = Case(number="LAB-AUDIT", title="lab", owner_id=user.id)
        session.add(case)
        await session.flush()
        evidence = Evidence(
            case_id=case.id,
            filename="sample.pdf",
            sha256="c" * 64,
            content_type="application/pdf",
            size_bytes=10,
            storage_key="k",
            uploaded_by=user.id,
        )
        session.add(evidence)
        await session.commit()
        case_id, evidence_id = case.id, evidence.id

    await client.post(
        f"/cases/{case_id}/analyses",
        json={"evidence_id": str(evidence_id), "module": "toxicology"},
        headers=headers,
    )

    # Admin-only and case-spanning: the audit log covers every case, so it is not
    # reachable through the officer's case-scoped routes.
    admin = await _auth(client, seed_users["admin"])
    listed = await client.get("/cases/audit", headers=admin)
    assert listed.status_code == 200, listed.text
    refused = [
        entry
        for entry in listed.json()
        if entry["action"] == "analysis.refused" and entry["payload"]["module"] == "toxicology"
    ]
    assert refused, f"the refusal was not recorded in the audit chain: {listed.text[:400]}"


async def test_the_catalog_is_served_and_never_looks_runnable(
    client: AsyncClient,
    seed_users: dict,
) -> None:
    """Examiners can find the out-of-scope techniques.

    Served from its own endpoint rather than mixed into `/modules`, because a
    catalog entry with no scoring path is not an analyzer and listing it beside
    runnable ones would imply it can be queued.
    """
    headers = await _auth(client, seed_users["officer"])

    catalog = await client.get("/lab-techniques", headers=headers)
    assert catalog.status_code == 200, catalog.text
    entries = catalog.json()
    assert {e["name"] for e in entries} == set(lab_only.BY_NAME)
    for entry in entries:
        # Nothing here may read as a module spec: no threshold, no tier, no
        # scoreable flag. A UI that found a `threshold` would render a bar.
        assert set(entry) == {"name", "discipline", "why", "instead"}

    modules = await client.get("/modules", headers=headers)
    assert modules.status_code == 200
    assert not (
        {m["name"] for m in modules.json()} & set(lab_only.BY_NAME)
    ), "a Tier-C technique appeared in the runnable module catalog"


def test_tier_c_is_not_reported_as_unsupported() -> None:
    """`unsupported` means "this host cannot run a real module".

    Provisioning weights would fix that one. Tier C has no module on any host, so
    filing it under `unsupported` would promise a fix that does not exist — and an
    operator reading the status would go looking for one.
    """
    assert lab_only.REFUSAL_PREFIX not in JobStatus.UNSUPPORTED.value
    # The refusal is not a job status at all; it is an HTTP 422 raised before a job
    # exists. Asserted as a constant so renaming either side forces a look.
    assert JobStatus.UNSUPPORTED.value == "unsupported"


def test_refusal_refuses_to_answer_for_an_unknown_module() -> None:
    """`refusal()` raises for a name that is not Tier C.

    Without this, a caller could hand an examiner the full Tier-C refusal text for a
    module that is merely absent — telling someone their DNA request is refused by
    design when in fact they mistyped a module name.
    """
    assert not lab_only.is_lab_only("byte-entropy")
    assert not lab_only.is_lab_only("no-such-module")
    with pytest.raises(KeyError):
        lab_only.refusal("byte-entropy")


def test_tier_c_never_appears_in_the_runnable_module_registry() -> None:
    """The strongest form of the property: there is no module to call.

    A Tier-C technique appearing in the registry would mean something could invoke
    it, and the router's refusal would be a single check standing between the
    catalog and a score.
    """
    from app.analysis import registry

    registry.register_all()
    registered = set(registry.registered_names())
    assert not (registered & set(lab_only.BY_NAME)), (
        f"Tier-C techniques are registered as analyzers: "
        f"{sorted(registered & set(lab_only.BY_NAME))}"
    )


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    response = await client.post("/auth/login", json=creds)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

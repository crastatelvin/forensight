"""FI-4 — cross-case entity links.

The property that matters most here is negative: a link must never disclose that a
value appears in a case the caller has no right to open. That is the reason the scope
predicate goes onto the SQL join rather than being applied to the results afterwards,
and it is the thing these tests are mostly about.

The second thing worth pinning is what matching does *not* do. Everything here matches
exactly on a case-folded, whitespace-collapsed value, because a confidence score on a
guess reads as a judgement — and in a forensic tool that is the failure mode, not a
feature.
"""

from __future__ import annotations

import uuid

import pytest
from app.auth.rbac import CurrentUser
from app.auth.roles import Role
from app.domain import links as lk
from app.domain.models import Case, Entity
from app.domain.workflow_repo import create_entity
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _user(role: Role, user_id: uuid.UUID | None = None) -> CurrentUser:
    """A `CurrentUser`. Pass `user_id` when the caller is meant to own the cases.

    The first version always generated a fresh id, so the "both cases owned by one
    officer" test was silently testing an officer who owned neither — and it passed,
    because it asserted a link appeared and the scoping was so tight that... no. It
    failed, which is the only reason this is worth mentioning: the assertion was right
    and the fixture was wrong.
    """
    return CurrentUser(user_id=str(user_id or uuid.uuid4()), role=role)


def _case(number: str, owner: uuid.UUID, assignee: uuid.UUID | None = None) -> Case:
    return Case(
        number=number,
        title=number,
        description="",
        status="intake",
        owner_id=owner,
        assigned_to=assignee,
        priority="normal",
    )


async def _with_entity(
    session: AsyncSession, case: Case, kind: str, value: str
) -> tuple[Case, Entity]:
    entity = await create_entity(session, case_id=case.id, kind=kind, value=value)
    return case, entity


# --- normalisation, which is the only matching that happens ---------------


def test_normalisation_folds_case_and_whitespace_only() -> None:
    assert lk.normalise("  Jane   Doe ") == "jane doe"
    assert lk.normalise("JANE DOE") == lk.normalise("jane doe")
    # Unicode form, not just case: the same name from two machines can arrive in
    # different normalisations, and byte equality would report two people as
    # unrelated because of their keyboard configuration.
    # Explicit escapes: typing the two forms literally lets an editor normalise them
    # to the same bytes, which would make this test assert nothing.
    assert lk.normalise("J\u00f6rg") == lk.normalise("Jo\u0308rg")


def test_normalisation_does_not_reorder_or_strip_punctuation() -> None:
    """Deciding two different strings are the same thing is not a normalisation."""
    assert lk.normalise("Doe, Jane") != lk.normalise("Jane Doe")
    assert lk.normalise("J. Doe") != lk.normalise("Jane Doe")


# --- linking ---------------------------------------------------------------


async def test_a_repeated_entity_links_across_two_cases(sessionmaker_fixture) -> None:
    officer = uuid.uuid4()
    async with sessionmaker_fixture() as session:
        first = _case("A-1", officer)
        session.add(first)
        await session.flush()
        await create_entity(session, case_id=first.id, kind="person", value="Jane Doe")

        second = _case("B-1", officer)
        session.add(second)
        await session.flush()
        await create_entity(session, case_id=second.id, kind="person", value="  jane  doe ")

        found = await lk.find_links(session, case_id=first.id, user=_user(Role.ADMIN), limit=100)

    assert len(found) == 1
    link = found[0]
    assert link.kind == "person"
    assert link.case_count == 2
    assert {number for _, number, _ in link.occurrences} == {"A-1", "B-1"}


async def test_a_value_unique_to_one_case_produces_no_link(sessionmaker_fixture) -> None:
    officer = uuid.uuid4()
    async with sessionmaker_fixture() as session:
        first = _case("A-1", officer)
        session.add(first)
        await session.flush()
        await create_entity(session, case_id=first.id, kind="person", value="Jane Doe")
        second = _case("B-1", officer)
        session.add(second)
        await session.flush()
        await create_entity(session, case_id=second.id, kind="person", value="Someone Else")

        found = await lk.find_links(session, case_id=first.id, user=_user(Role.ADMIN), limit=100)
    assert found == []


async def test_the_same_value_under_different_kinds_does_not_link(sessionmaker_fixture) -> None:
    """A name that is a person in one case and a location label in another."""
    officer = uuid.uuid4()
    async with sessionmaker_fixture() as session:
        first = _case("A-1", officer)
        session.add(first)
        await session.flush()
        await create_entity(session, case_id=first.id, kind="person", value="Lantern")
        second = _case("B-1", officer)
        session.add(second)
        await session.flush()
        await create_entity(session, case_id=second.id, kind="location", value="Lantern")

        found = await lk.find_links(session, case_id=first.id, user=_user(Role.ADMIN), limit=100)
    assert found == []


async def test_similar_but_different_values_do_not_link(sessionmaker_fixture) -> None:
    """The negative case that justifies refusing fuzzy matching.

    "Jane Doe" and "Jane Do" are one character apart. An edit-distance matcher would
    score them as a near-certain match and a reader would take it as a finding; here
    they simply do not link, which is the honest answer.
    """
    officer = uuid.uuid4()
    async with sessionmaker_fixture() as session:
        first = _case("A-1", officer)
        session.add(first)
        await session.flush()
        await create_entity(session, case_id=first.id, kind="person", value="Jane Doe")
        second = _case("B-1", officer)
        session.add(second)
        await session.flush()
        await create_entity(session, case_id=second.id, kind="person", value="Jane Do")

        found = await lk.find_links(session, case_id=first.id, user=_user(Role.ADMIN), limit=100)
    assert found == []


async def test_a_value_recorded_twice_in_one_case_is_one_link(sessionmaker_fixture) -> None:
    """Two rows are not two findings."""
    officer = uuid.uuid4()
    async with sessionmaker_fixture() as session:
        first = _case("A-1", officer)
        session.add(first)
        await session.flush()
        await create_entity(session, case_id=first.id, kind="person", value="Jane Doe")
        await create_entity(session, case_id=first.id, kind="person", value="jane doe")
        second = _case("B-1", officer)
        session.add(second)
        await session.flush()
        await create_entity(session, case_id=second.id, kind="person", value="Jane Doe")

        found = await lk.find_links(session, case_id=first.id, user=_user(Role.ADMIN), limit=100)
    assert len(found) == 1


# --- the access-control property ------------------------------------------


async def test_a_link_to_a_case_the_caller_cannot_see_is_omitted(sessionmaker_fixture) -> None:
    """The load-bearing test.

    An officer owns case A and sees nothing of case B. Case B contains the same name.
    The link must not appear — not "redacted", not "1 further occurrence elsewhere":
    absent. Reporting even its existence would confirm both that case B is real and
    that it contains this value, which is precisely what the 404-on-out-of-scope rule
    refuses elsewhere in this API.
    """
    officer = uuid.uuid4()
    stranger = uuid.uuid4()
    async with sessionmaker_fixture() as session:
        mine = _case("A-1", officer)
        session.add(mine)
        await session.flush()
        await create_entity(session, case_id=mine.id, kind="person", value="Jane Doe")

        theirs = _case("B-1", stranger)
        session.add(theirs)
        await session.flush()
        await create_entity(session, case_id=theirs.id, kind="person", value="Jane Doe")

        found = await lk.find_links(session, case_id=mine.id, user=_user(Role.OFFICER), limit=100)

    assert found == [], f"a link leaked a case the caller cannot see: {found}"
    assert "B-1" not in str(found)


async def test_a_reviewer_and_an_admin_see_the_link_the_officer_cannot(
    sessionmaker_fixture,
) -> None:
    """Same data, different scope — so the difference is authorisation, not the query."""
    officer = uuid.uuid4()
    stranger = uuid.uuid4()
    async with sessionmaker_fixture() as session:
        mine = _case("A-1", officer)
        session.add(mine)
        await session.flush()
        await create_entity(session, case_id=mine.id, kind="person", value="Jane Doe")
        theirs = _case("B-1", stranger)
        session.add(theirs)
        await session.flush()
        await create_entity(session, case_id=theirs.id, kind="person", value="Jane Doe")

        officer_view = await lk.find_links(
            session, case_id=mine.id, user=_user(Role.OFFICER, officer), limit=100
        )
        admin_view = await lk.find_links(
            session, case_id=mine.id, user=_user(Role.ADMIN), limit=100
        )
        reviewer_view = await lk.find_links(
            session, case_id=mine.id, user=_user(Role.REVIEWER), limit=100
        )

    assert officer_view == []
    assert len(admin_view) == 1
    assert len(reviewer_view) == 1


async def test_a_link_between_two_cases_both_owned_is_visible(sessionmaker_fixture) -> None:
    """The positive control: the scope filter must not be over-tight."""
    officer = uuid.uuid4()
    async with sessionmaker_fixture() as session:
        first = _case("A-1", officer)
        second = _case("B-1", officer)
        session.add_all([first, second])
        await session.flush()
        await create_entity(session, case_id=first.id, kind="person", value="Jane Doe")
        await create_entity(session, case_id=second.id, kind="person", value="Jane Doe")

        found = await lk.find_links(
            session, case_id=first.id, user=_user(Role.OFFICER, officer), limit=100
        )
    assert len(found) == 1, "the scope filter must not be over-tight for cases the caller owns"


# --- what a link claims ----------------------------------------------------


def test_a_link_says_it_is_a_candidate_and_not_an_identification() -> None:
    link = lk.EntityLink(
        kind="person",
        value="Jane Doe",
        occurrences=((uuid.uuid4(), "A-1", uuid.uuid4()),),
    ).to_dict()
    assert link["is_candidate_only"] is True
    assert "NOT an identification" in link["disclaimer"]
    assert "Two people can share a name" in link["disclaimer"]


def test_the_description_says_what_matching_does_not_attempt() -> None:
    described = lk.describe()
    assert "exact equality" in described["matches_on"]
    assert any("fuzzy" in item for item in described["not_attempted"])
    assert "omitted entirely" in described["scoping"]


# --- the endpoint ----------------------------------------------------------


async def test_the_endpoint_requires_read_access_to_the_case(
    client: AsyncClient, seed_users
) -> None:
    login = await client.post("/auth/login", json=seed_users["officer"])
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = await client.post("/cases", json={"number": "LNK-1", "title": "x"}, headers=headers)
    case_id = created.json()["id"]

    response = await client.get(f"/cases/{case_id}/entities/links", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    # The explanation travels with the answer rather than living in a client copy.
    assert "exact equality" in body["matches_on"]
    assert "NOT an identification" in body["disclaimer"]


async def test_searching_for_links_is_audited(client: AsyncClient, seed_users) -> None:
    """The results are legitimately visible; the act of asking is still a record."""
    officer = await client.post("/auth/login", json=seed_users["officer"])
    headers = {"Authorization": f"Bearer {officer.json()['access_token']}"}
    created = await client.post("/cases", json={"number": "LNK-2", "title": "x"}, headers=headers)
    await client.get(f"/cases/{created.json()['id']}/entities/links", headers=headers)

    admin = await client.post("/auth/login", json=seed_users["admin"])
    audit = await client.get(
        "/cases/audit?limit=200",
        headers={"Authorization": f"Bearer {admin.json()['access_token']}"},
    )
    entries = [e for e in audit.json() if e["action"] == "entity.links.read"]
    assert entries, "a cross-case link search left no trace"
    assert entries[0]["payload"]["case_id"]

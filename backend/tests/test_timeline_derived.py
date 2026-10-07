"""FI-1 — the derived case timeline.

The timeline tab shipped permanently empty. Its empty state promised "events appear as
evidence is uploaded, analyses run and findings are recorded", and nothing did any of
those things: `POST /cases/{id}/timeline` existed and was reachable from no UI at all.
So a case's chronology was something an examiner could see only by calling the API by
hand.

These go through the real HTTP API rather than calling the helpers directly, because
the properties that matter here are mostly about the *contract*: which entries appear,
whether a derived one can be told from an examiner's, and whether anything is
persisted. Calling `derive_events` directly would not have caught a derived id that
collided with a real one, or a `derived` flag the serializer dropped.

The parts that are easy to get wrong:

- derived entries are **not written to `timeline_events`**, so they cannot become
  permanent records of what this code happened to think;
- a derived entry is distinguishable from an examiner's, and an examiner cannot forge
  that distinction by writing a label that looks system-generated;
- an event with no known time stays visible and stays last;
- the derived view is opt-in, because these are handling records rather than findings.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.domain import timeline as tl
from app.domain.models import Case, ChainOfCustody, Evidence, TimelineEvent
from httpx import AsyncClient
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    response = await client.post("/auth/login", json=creds)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _new_case(client: AsyncClient, headers: dict[str, str], seed_users) -> str:
    response = await client.post(
        "/cases",
        json={"title": "derived timeline", "number": f"FI1-{uuid.uuid4().hex[:8].upper()}"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _upload(
    client: AsyncClient,
    headers: dict[str, str],
    case_id: str,
    fake_store,
    *,
    filename: str = "photo.jpg",
    body: bytes = b"\xff\xd8\xff\xe0fake jpeg bytes",
) -> str:
    response = await client.post(
        f"/cases/{case_id}/evidence",
        files={"file": (filename, body, "image/jpeg")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_an_uploaded_file_produces_a_derived_entry(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """The regression: the timeline was empty because nothing derived from the case."""
    headers = await _auth(client, seed_users["officer"])
    case_id = await _new_case(client, headers, seed_users)
    await _upload(client, headers, case_id, fake_store)

    plain = await client.get(f"/cases/{case_id}/timeline", headers=headers)
    assert plain.status_code == 200
    assert plain.json() == [], "derived entries must be opt-in, so the default view is unchanged"

    derived = await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)
    assert derived.status_code == 200
    body = derived.json()
    assert body, "an uploaded file must appear on the derived timeline"
    assert any(e["derived"] for e in body)
    assert any("photo.jpg" in e["label"] for e in body)


async def test_nothing_is_written_to_the_timeline_table(
    client: AsyncClient, seed_users, fake_store, sessionmaker_fixture
) -> None:
    """Derivation must not persist anything.

    `timeline_events` is the examiner's record. A derived row written there would be
    indistinguishable from their own entry, would survive this code being corrected,
    and could not be retracted. Asserted because that is what makes the design safe.
    """
    headers = await _auth(client, seed_users["officer"])
    case_id = await _new_case(client, headers, seed_users)
    await _upload(client, headers, case_id, fake_store)

    await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)

    async with sessionmaker_fixture() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(TimelineEvent)
            .where(TimelineEvent.case_id == uuid.UUID(case_id))
        )
    assert count == 0


async def test_a_derived_entry_is_marked_and_an_examiner_entry_is_not(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """The distinction is a field, not something read out of the label."""
    headers = await _auth(client, seed_users["officer"])
    case_id = await _new_case(client, headers, seed_users)
    await _upload(client, headers, case_id, fake_store)

    # A label deliberately shaped like a derived entry, written by a person.
    posted = await client.post(
        f"/cases/{case_id}/timeline",
        json={
            "label": "Evidence received: photo.jpg",
            "occurred_at": datetime.now(UTC).isoformat(),
            "source_ref": "manual",
        },
        headers=headers,
    )
    assert posted.status_code == 201, posted.text

    body = (await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)).json()
    same_label = [e for e in body if e["label"] == "Evidence received: photo.jpg"]

    # Both entries carry the identical label. Only the system one is derived, so an
    # examiner cannot pass as the platform by choosing their words.
    assert len(same_label) >= 2, "an examiner's entry must not merge into a derived one"
    assert sum(1 for e in same_label if e["derived"]) == 1


async def test_an_undated_entry_stays_visible_and_carries_its_own_time_flag(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """`occurred_at` is nullable. Dropping the entry hides the examiner's work."""
    headers = await _auth(client, seed_users["officer"])
    case_id = await _new_case(client, headers, seed_users)
    posted = await client.post(
        f"/cases/{case_id}/timeline",
        json={"label": "undated entry", "uncertain": True, "source_ref": "note"},
        headers=headers,
    )
    assert posted.status_code == 201
    assert posted.json()["occurred_at"] is None

    body = (await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)).json()
    undated = [e for e in body if e["label"] == "undated entry"]
    assert len(undated) == 1
    assert (
        undated[0]["occurred_at"] is None
    ), "the absence of a time must be reported, not filled in"
    assert undated[0]["uncertain"] is True, "the examiner's own uncertainty must survive"
    assert undated[0]["derived"] is False


async def test_a_derived_entry_is_never_marked_uncertain(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """`uncertain` means the examiner was unsure. A system record is not unsure."""
    headers = await _auth(client, seed_users["officer"])
    case_id = await _new_case(client, headers, seed_users)
    await _upload(client, headers, case_id, fake_store)

    body = (await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)).json()
    derived = [e for e in body if e["derived"]]
    assert derived
    assert all(e["uncertain"] is False for e in derived), (
        "marking derived entries uncertain would dilute the flag, which exists to "
        "mean that a person was not sure of something they wrote"
    )


async def test_derived_ids_are_stable_and_do_not_collide_with_recorded_ones(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """Stable ids, because React keys the list by them; distinct from real rows.

    A regenerated id would remount every entry on each refresh, discarding focus. A
    colliding id would let a derived entry be mistaken for a recorded one by anything
    comparing ids.
    """
    headers = await _auth(client, seed_users["officer"])
    case_id = await _new_case(client, headers, seed_users)
    await _upload(client, headers, case_id, fake_store)
    recorded = await client.post(
        f"/cases/{case_id}/timeline",
        json={"label": "recorded", "occurred_at": datetime.now(UTC).isoformat()},
        headers=headers,
    )

    first = (await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)).json()
    second = (await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)).json()
    assert [e["id"] for e in first] == [
        e["id"] for e in second
    ], "ids must be stable across requests"

    ids = [e["id"] for e in first]
    assert len(ids) == len(set(ids)), "two entries shared an id"
    assert recorded.json()["id"] in ids, (
        "a recorded entry must keep its real row id — an earlier version rebuilt every "
        "entry through the synthetic-id helper, so the id a client would use to fetch "
        "or cite a recorded event pointed at a row that does not exist"
    )


async def test_entries_come_back_oldest_first(client: AsyncClient, seed_users, fake_store) -> None:
    """Matching the backend's own `asc().nullslast()`, which the UI inverts for display."""
    headers = await _auth(client, seed_users["officer"])
    case_id = await _new_case(client, headers, seed_users)
    base = datetime.now(UTC)
    for offset, label in ((3, "third"), (1, "first"), (2, "second")):
        await client.post(
            f"/cases/{case_id}/timeline",
            json={"label": label, "occurred_at": (base - timedelta(hours=offset)).isoformat()},
            headers=headers,
        )

    body = (await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)).json()
    dated = [e["occurred_at"] for e in body if e["occurred_at"]]
    assert dated == sorted(dated)


def test_the_derived_view_is_opt_in_by_default() -> None:
    """The endpoint contract: existing consumers keep seeing exactly what they saw."""
    from app.domain.router import get_timeline

    assert inspect.signature(get_timeline).parameters["derived"].default is False


async def test_one_arrival_appears_once_not_twice(
    client: AsyncClient, seed_users, fake_store
) -> None:
    """The upload is recorded twice — on the evidence row and in custody.

    Reporting both would put the same arrival on the timeline twice with identical
    wording, which reads as two occurrences. Exactly one entry per upload.
    """
    headers = await _auth(client, seed_users["officer"])
    case_id = await _new_case(client, headers, seed_users)
    await _upload(client, headers, case_id, fake_store)

    body = (await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)).json()
    arrivals = [e for e in body if "Evidence received" in e["label"]]
    assert len(arrivals) == 1, f"the upload appeared {len(arrivals)} times"

    # A *second, different* file is a second arrival, so this is deduplication rather
    # than a filter that quietly drops all but the first file. Distinct bytes matter:
    # ingest deduplicates on content hash, so re-uploading identical bytes would
    # return the first evidence row and prove nothing.
    await _upload(
        client,
        headers,
        case_id,
        fake_store,
        filename="second.jpg",
        body=b"\xff\xd8\xff\xe0a different file entirely",
    )
    body = (await client.get(f"/cases/{case_id}/timeline?derived=true", headers=headers)).json()
    assert len([e for e in body if "Evidence received" in e["label"]]) == 2


async def test_custody_bookkeeping_is_excluded_and_the_allowlist_is_written_down(
    sessionmaker_fixture,
) -> None:
    """An allowlist, so what drops off the timeline is stated rather than implied."""
    async with sessionmaker_fixture() as session:
        # `number` is required: the API assigns it on create, but a row built here
        # has to supply it.
        case = Case(
            title="custody allowlist",
            description="",
            number=f"FI1-{uuid.uuid4().hex[:8].upper()}",
            owner_id=uuid.uuid4(),
        )
        session.add(case)
        await session.flush()
        evidence = Evidence(
            case_id=case.id,
            filename="photo.jpg",
            sha256=uuid.uuid4().hex * 2,
            content_type="image/jpeg",
            size_bytes=10,
            storage_key=f"evidence/{uuid.uuid4().hex}",
            uploaded_by=uuid.uuid4(),
        )
        session.add(evidence)
        await session.flush()
        for action in ("upload", "access", "upload-dedupe", "internal-note"):
            session.add(
                ChainOfCustody(
                    evidence_id=evidence.id,
                    actor_id=uuid.uuid4(),
                    action=action,
                    detail=action,
                )
            )
        await session.flush()

        events = await tl.derive_events(session, case.id, evidence_by_id={evidence.id: evidence})
        custody = [e for e in events if e.source_ref.startswith("custody:")]

    assert len(custody) == 1, (
        "only access belongs here: an upload is reported from the Evidence row, and "
        "reporting both put one arrival on the timeline twice with identical wording, "
        "which reads as two separate occurrences"
    )
    assert tl._TIMELINE_CUSTODY_ACTIONS == ("access",)


def test_the_view_explains_itself() -> None:
    """The distinction is served, not written into the UI as a second copy."""
    joined = " ".join(tl.describe().values())
    assert "not when it happened" in joined
    assert "time unknown" in joined

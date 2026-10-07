"""Derive a case timeline from records the case already holds (FI-1).

**Derived on read, never written to `timeline_events`.** The table is the examiner's
own record: entries they made a judgement about, some of them marked `uncertain`
because they were not sure. Writing derived rows into it would mix two things that
look identical in the UI and are not the same at all — what the system observed
against what a person concluded. It would also mean a derived row became permanent
evidence of an event that later turns out to be an artefact of this code, with no way
to retract it. So `TimelineEvent` stays untouched and this returns a view.

**The timestamps here are when the platform did something, not when the event
happened.** An upload row says the file arrived at 14:02; it says nothing about when
the photograph was taken. A case timeline that blurs the two would let an examiner
read a processing time as an event time, which is the specific confusion a forensic
timeline exists to prevent. Every derived entry therefore names what it is a
timestamp *of* — "evidence received", not "photograph taken" — and carries
`derived=True` so the UI can say so.

**EXIF capture times are deliberately excluded.** `image-exif` records which fields
were *present*, not their values, so a capture timestamp is not available without
re-reading the bytes. It would be easy to fetch the object store here and parse the
EXIF again; that is refused. A module already read those bytes and chose what to
store, and a second reader in the request path would quietly turn the timeline into
an unbounded object-store scan on a list endpoint.

**Not a reconstruction of what happened.** This is a record of handling: what was
added, analysed, concluded, and by whom. Where the events of the case itself came
from is still the examiner's judgement to record.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ChainOfCustody, Evidence, Finding, TimelineEvent

#: Custody actions worth placing on a handling timeline.
#:
#: **`upload` is deliberately absent.** It is not a missing action — it is reported
#: from the `Evidence` row instead, and an earlier version reported both, so one
#: arrival appeared on the timeline twice with identical wording. Two entries saying
#: the same thing is worse than one entry: it reads as two separate occurrences, which
#: is precisely the misreading a timeline exists to prevent.
#:
#: An allowlist rather than every row, because `chain_of_custody` also records
#: `upload-dedupe` and internal bookkeeping. Dropping rows is a judgement, so it is
#: written down here rather than left implicit.
_TIMELINE_CUSTODY_ACTIONS = ("access",)


@dataclass(frozen=True)
class DerivedEvent:
    """One derived entry. Not an ORM row and never persisted."""

    occurred_at: datetime
    label: str
    source_ref: str
    #: Always True. Distinguishes a derived entry from an examiner's own, which the
    #: UI must be able to say without inferring it from the label text.
    derived: bool = True

    def sort_key(self) -> tuple[datetime, str]:
        return (self.occurred_at, self.label)


@dataclass(frozen=True)
class TimelineEntry:
    """A merged entry: derived or recorded, with the id a client can act on."""

    #: The real row id for a recorded entry, `None` for a derived one. `None` is the
    #: signal to synthesise, and it is deliberately not a fake uuid — a fake id on a
    #: derived entry and a real id on a recorded one have to be distinguishable.
    id: uuid.UUID | None
    occurred_at: datetime
    label: str
    source_ref: str
    derived: bool


async def derive_events(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    evidence_by_id: dict[uuid.UUID, Evidence] | None = None,
    limit: int = 500,
) -> list[DerivedEvent]:
    """Events derived from evidence, custody, analyses and findings.

    `limit` bounds the *query* per source rather than slicing the merged result: a
    timeline assembled from five sources cannot be paged correctly by trimming the
    concatenation, because which entries survive would depend on the order the
    sources happened to be appended in. Each source is asked for its most recent
    `limit` rows, so the cut is the newest of each kind.
    """
    evidence_by_id = evidence_by_id or {}
    events: list[DerivedEvent] = []

    if evidence_by_id:
        for item in sorted(evidence_by_id.values(), key=lambda e: e.created_at):
            events.append(
                DerivedEvent(
                    occurred_at=item.created_at,
                    # "received" rather than "captured" or "created": this is when the
                    # platform took custody, which is the only thing this row knows.
                    label=f"Evidence received: {item.filename}",
                    source_ref=str(item.id),
                )
            )

    if evidence_by_id:
        evidence_ids = list(evidence_by_id)
        custody = await session.execute(
            select(ChainOfCustody)
            .where(
                ChainOfCustody.evidence_id.in_(evidence_ids),
                ChainOfCustody.action.in_(_TIMELINE_CUSTODY_ACTIONS),
            )
            .order_by(ChainOfCustody.created_at.desc())
            .limit(limit)
        )
        for custody_row in custody.scalars():
            name = evidence_by_id[custody_row.evidence_id].filename
            events.append(
                DerivedEvent(
                    occurred_at=custody_row.created_at,
                    label=f"Evidence accessed: {name}",
                    source_ref=f"custody:{custody_row.id}",
                )
            )

    from app.analysis.models import AnalysisResult

    results = await session.execute(
        select(AnalysisResult)
        .where(AnalysisResult.case_id == case_id)
        .order_by(AnalysisResult.created_at.desc())
        .limit(limit)
    )
    for result_row in results.scalars():
        events.append(
            DerivedEvent(
                occurred_at=result_row.created_at,
                label=f"Analysis completed: {result_row.module}",
                source_ref=f"result:{result_row.id}",
            )
        )

    findings = await session.execute(
        select(Finding)
        .where(Finding.case_id == case_id)
        .order_by(Finding.created_at.desc())
        .limit(limit)
    )
    for finding_row in findings.scalars():
        events.append(
            DerivedEvent(
                occurred_at=finding_row.created_at,
                label=f"Finding recorded: {finding_row.title}",
                source_ref=f"finding:{finding_row.id}",
            )
        )

    # `created_at` can tie — several analyses finishing in the same millisecond is
    # ordinary — so the label breaks the tie to keep the order stable across calls.
    events.sort(key=DerivedEvent.sort_key)
    return events


def merge(derived: list[DerivedEvent], recorded: list[TimelineEvent]) -> list[TimelineEntry]:
    """Recorded and derived entries together, oldest first, undated last.

    Matches the backend's own `asc().nullslast()` ordering so the two agree, and the
    frontend inverts that for display.

    Recorded entries keep their **real row id**. An earlier version rebuilt every entry
    through the synthetic-id helper, so a recorded event came back with a derived id
    that resolved to nothing — the id a client would use to fetch, link or cite that
    event pointed at a row that does not exist. Only entries that genuinely have no
    row get a synthetic id.
    """
    combined: list[TimelineEntry] = [
        TimelineEntry(
            id=None,
            occurred_at=event.occurred_at,
            label=event.label,
            source_ref=event.source_ref,
            derived=True,
        )
        for event in derived
    ]
    combined.extend(
        TimelineEntry(
            id=row.id,
            occurred_at=row.occurred_at,
            label=row.label,
            source_ref=row.source_ref,
            derived=False,
        )
        for row in recorded
        if row.occurred_at is not None
    )
    combined.sort(key=lambda entry: (entry.occurred_at, entry.label))
    return combined


def recorded_undated(recorded: list[TimelineEvent]) -> list[TimelineEvent]:
    """Recorded entries with no time, kept out of `merge` so they can be shown last.

    They are returned separately rather than dropped: hiding an entry the examiner
    wrote is worse than showing it in the wrong place, and the UI already labels
    these "time unknown".
    """
    return [row for row in recorded if row.occurred_at is None]


def describe() -> dict[str, Any]:
    """What this view is, for the API response.

    Served rather than written into the UI so the explanation of what a derived
    timeline is — and is not — has one owner.
    """
    return {
        "derived_events_explain": (
            "Events derived from this case's own records: evidence received, "
            "evidence accessed, analyses completed, findings recorded. Their "
            "timestamps are when the platform recorded the event, not when it "
            "happened in the world. EXIF capture times are not included."
        ),
        "recorded_events_explain": (
            "Events entered by an examiner. An event with no known time is shown "
            "as 'time unknown' and placed last, because placing it among dated "
            "events would assert an order the record does not support."
        ),
    }

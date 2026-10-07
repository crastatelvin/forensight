"""Multi-case entity linking (FI-4).

The same person, vehicle, device or address turning up in two investigations is one of
the more valuable things a case system can surface, and it is invisible while entities
are scoped to a single case. This finds entities that recur across the cases a caller
can already see.

**A link is a candidate, never an identity.** Two cases both containing
`person: "Jane Doe"` is evidence that two people with the same name were recorded, not
that they are the same person — the same is true of a device serial that a vendor
recycles and an address that changes hands. The output is a *prompt to look*, and it
says so in every field a client will render.

**Matching is exact on a normalised value, deliberately.** Case-folding and whitespace
collapsing are applied because they are not judgement calls: `"Jane Doe"` and
`" jane  doe "` are the same string to any reader. Nothing beyond that is applied — no
edit distance, no initials, no phonetic matching. Fuzzy name matching in a forensic
tool produces confident wrong links, and the confidence is what makes them dangerous:
an examiner who sees "88% match" reads a judgement where the system only has a guess.
A link that needs a human to accept it should say so, not quantify its own uncertainty.

**Cross-case reads are the whole risk here**, and it is handled by construction: the
query narrows to `case_scope_filter` *before* any entity is read, so a caller cannot
learn that a name appears in a case they have no right to open. A co-occurrence is
reported only when the caller can see **both** cases — reporting "this name also
appears somewhere you cannot see" would confirm the existence and content of a case
they were refused, which is exactly what the 404-on-out-of-scope rule exists to
prevent. The predicate is applied in SQL rather than by filtering afterwards, so the
unauthorised rows are never loaded in the first place.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.auth.rbac import CurrentUser
from app.domain.access import case_scope_filter
from app.domain.models import Case, Entity

#: Whitespace runs, collapsed to one space.
_WHITESPACE = re.compile(r"\s+")

DISCLAIMER = (
    "Cross-case entity link — the same kind and value recorded in more than one case "
    "you can see. This is NOT an identification of a person, vehicle or device. Two "
    "people can share a name, manufacturers recycle serial numbers, and addresses "
    "change hands, so a link is a prompt for a human to look rather than a finding. "
    "Matching is exact on a case-folded, whitespace-collapsed value; nothing fuzzy is "
    "attempted, because a confident-looking score on a guess is worse than no link."
)


def normalise(value: str) -> str:
    """Case-folded, whitespace-collapsed, NFC-normalised.

    The Unicode normalisation matters for entity resolution specifically: the same
    name typed on two machines can arrive in different normal forms, and byte equality
    would report two people as unrelated because of how their keyboards were
    configured.

    Deliberately *not* applied: punctuation stripping, name reordering, and anything
    that decides two different strings are the same thing.
    """
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value).strip()).casefold()


@dataclass(frozen=True)
class EntityLink:
    """One recurring (kind, value) pair, and everywhere it was recorded."""

    kind: str
    value: str
    #: `(case_id, case_number, entity_id)` per occurrence, sorted by case number so
    #: the order is stable and two requests cannot disagree about it.
    occurrences: tuple[tuple[uuid.UUID, str, uuid.UUID], ...]

    @property
    def case_count(self) -> int:
        return len({case_id for case_id, _, _ in self.occurrences})

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "case_count": self.case_count,
            "occurrences": [
                {"case_id": str(case_id), "case_number": number, "entity_id": str(entity_id)}
                for case_id, number, entity_id in self.occurrences
            ],
            "is_candidate_only": True,
            "disclaimer": DISCLAIMER,
        }


def describe() -> dict[str, Any]:
    """What a link is, served rather than duplicated into a client."""
    return {
        "matches_on": "exact equality of a case-folded, whitespace-collapsed value",
        "not_attempted": [
            "edit distance or fuzzy name matching",
            "initials or phonetic comparison",
            "any inference that two records describe the same real-world thing",
        ],
        "why_not_fuzzy": (
            "a confidence score on a guess reads as a judgement; in a forensic tool "
            "that is the failure mode worth avoiding, not a feature"
        ),
        "scoping": (
            "only cases the caller may already open are read or reported; a "
            "co-occurrence involving an invisible case is omitted entirely rather than "
            "hinted at"
        ),
        "disclaimer": DISCLAIMER,
    }


async def find_links(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    user: CurrentUser,
    limit: int = 100,
) -> list[EntityLink]:
    """Entities from `case_id` that also appear in another case the caller can see.

    `limit` bounds how many of *this* case's entities are used as seeds, not how many
    links come back. Trimming the results instead would make which links survive depend
    on the order rows happened to arrive in.
    """
    seeds = (
        (
            await session.execute(
                select(Entity)
                .where(Entity.case_id == case_id)
                .order_by(Entity.created_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    if not seeds:
        return []

    keys = {(seed.kind, normalise(seed.value)) for seed in seeds}
    kinds = {kind for kind, _ in keys}
    normalised_values = {value for _, value in keys}

    # **The scope predicate goes on the Case join, not after it.** Filtering linked
    # cases in Python would load them first: an officer would still have caused an
    # unauthorised case to be read off disk, and only the response would be scrubbed.
    scope: ColumnElement[bool] | None = case_scope_filter(user)
    case_stmt = select(Case.id, Case.number)
    if scope is not None:
        case_stmt = case_stmt.where(scope)

    visible_cases = {row.id: row.number for row in (await session.execute(case_stmt)).all()}
    if not visible_cases:
        return []

    # `normalise` is Python-side, so the candidate set is narrowed in SQL to the
    # distinct raw values that could normalise to a seed and then filtered exactly.
    candidates = (
        await session.execute(
            select(Entity, Case.number)
            .join(Case, Case.id == Entity.case_id)
            .where(Entity.kind.in_(kinds), Entity.case_id != case_id)
        )
    ).all()

    grouped: dict[tuple[str, str], list[tuple[uuid.UUID, str, uuid.UUID]]] = {}
    originals: dict[tuple[str, str], str] = {}
    for entity, number in candidates:
        if entity.case_id not in visible_cases:
            continue
        key = (entity.kind, normalise(entity.value))
        if key not in keys:
            continue
        grouped.setdefault(key, []).append((entity.case_id, number, entity.id))
        # The value as first recorded, not the folded one: a reader needs to recognise
        # it.
        originals.setdefault(key, entity.value)

    own_number = visible_cases.get(case_id, "")
    links: list[EntityLink] = []
    for seed in seeds:
        key = (seed.kind, normalise(seed.value))
        others = grouped.get(key)
        if not others:
            continue
        links.append(
            EntityLink(
                kind=seed.kind,
                value=originals.get(key, seed.value),
                occurrences=tuple(
                    sorted(
                        [(case_id, own_number, seed.id)] + others,
                        key=lambda row: (row[1], str(row[0])),
                    )
                ),
            )
        )

    # Deduplicate by key: a case may hold the same value twice, and reporting that as
    # two links would read as two separate findings.
    unique: dict[tuple[str, str], EntityLink] = {}
    for link in links:
        unique.setdefault((link.kind, normalise(link.value)), link)
    return sorted(unique.values(), key=lambda link: (link.kind, link.value))

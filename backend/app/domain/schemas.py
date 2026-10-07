"""Case & evidence API schemas (Phase 2/3)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models import Case

# Case numbers are quoted into a Content-Disposition header on export, so they
# must not contain quotes, CR/LF, or path separators. Keeping this a validation
# (not just sanitization) means the stored value stays the value the user typed.
_CASE_NUMBER_RE = re.compile(r"^[A-Za-z0-9._/ -]{1,64}$")


class CaseCreate(BaseModel):
    number: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=10000)
    priority: str = Field(default="normal", max_length=16)

    @field_validator("number")
    @classmethod
    def _check_number(cls, v: str) -> str:
        if not _CASE_NUMBER_RE.match(v):
            raise ValueError(
                "case number may contain only letters, digits, '.', '_', '-', "
                "'/', spaces and must not contain quotes or newlines"
            )
        return v


class CaseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=10000)
    priority: str | None = Field(default=None, max_length=16)
    due_at: datetime | None = None


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    title: str
    description: str
    status: str
    owner_id: uuid.UUID
    assigned_to: uuid.UUID | None
    priority: str
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime
    #: The status values this case may legally move to, given where it is now.
    #:
    #: Served rather than recomputed by clients: the workflow lives in one place
    #: (`CASE_TRANSITIONS`), and a UI that hard-codes its own copy will offer a
    #: move the API then rejects with 409 -- which is exactly the kind of
    #: disagreement that makes a tool untrustworthy during a live investigation.
    #: Empty for a terminal status, which is what makes "no moves available" a
    #: real answer rather than a missing one.
    allowed_transitions: list[str] = Field(default_factory=list)


class CaseTransition(BaseModel):
    target: str = Field(min_length=1, max_length=32)


def case_to_read(case: Case) -> CaseRead:
    """Serialise a Case, filling in the transitions it may legally make.

    One function rather than eight inline `model_validate` calls, because the
    derived field has to be filled in every time. A call site that forgets it would
    return a case claiming there are no moves, and a reviewer would conclude the
    case is closed when it is not.
    """
    from app.domain.state_machine import CASE_TRANSITIONS

    return CaseRead.model_validate(case).model_copy(
        update={
            "allowed_transitions": sorted(
                str(s) for s in CASE_TRANSITIONS.get(case.status, frozenset())
            )
        }
    )


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    filename: str
    sha256: str
    content_type: str
    size_bytes: int
    created_at: datetime


class CustodyEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor_id: uuid.UUID
    action: str
    detail: str
    created_at: datetime


class CaseAssign(BaseModel):
    assigned_to: uuid.UUID


class FindingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(default="", max_length=20000)
    #: Analyzer results this finding rests on.
    #:
    #: Optional, and a finding without them is created rather than refused — an
    #: examiner records an observation before they have the analysis to back it. But
    #: an id that does not resolve on this case is **rejected with a 422** rather
    #: than stored: a citation pointing at nothing would render in a signed report as
    #: if it pointed at something.
    citations: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class FindingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    body: str | None = Field(default=None, max_length=20000)
    status: str | None = Field(default=None, max_length=32)


class FindingCitationRead(BaseModel):
    """One cited result, as the UI and the report both render it."""

    model_config = ConfigDict(from_attributes=True)

    result_id: uuid.UUID
    evidence_id: uuid.UUID
    module: str
    score: float
    threshold: float
    tier: str
    model_version: str
    not_applicable: bool
    cited_at: datetime


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    title: str
    body: str
    status: str
    created_by: uuid.UUID
    created_at: datetime
    #: Present so a finding's sourcing is visible wherever the finding is, rather
    #: than only inside a generated PDF. An unsourced finding that looks identical
    #: to a sourced one is the problem; the field is what lets a caller see it.
    citations: list[FindingCitationRead] = Field(default_factory=list)

    @property
    def is_sourced(self) -> bool:
        return bool(self.citations)


class EntityCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=512)
    attributes: dict[str, object] = Field(default_factory=dict)


class EntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    kind: str
    value: str
    attributes: dict[str, object]
    created_at: datetime


class TimelineEventCreate(BaseModel):
    label: str = Field(min_length=1, max_length=512)
    occurred_at: datetime | None = None
    source_ref: str = Field(default="", max_length=512)
    uncertain: bool = False


class TimelineEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    occurred_at: datetime | None
    label: str
    source_ref: str
    uncertain: bool
    created_at: datetime
    #: True for an event derived from the case's own records, False for one an
    #: examiner entered. Defaults to False so an entry posted through the API is
    #: recorded as the examiner's own without having to say so.
    #:
    #: This is the only thing telling the two apart in the UI, and it is a field
    #: rather than something inferred from the label text — an examiner writing
    #: "Evidence received: photo.jpg" must not be able to pass as a system entry.
    derived: bool = False


class AuditEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: uuid.UUID | None
    action: str
    target: str
    payload: dict[str, object]
    prev_hash: str
    entry_hash: str
    created_at: datetime


class AuditChainStatus(BaseModel):
    """Result of recomputing the whole hash chain (T2.5)."""

    valid: bool
    entries: int

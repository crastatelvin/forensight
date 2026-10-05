"""Case & evidence API schemas (Phase 2/3)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class CaseTransition(BaseModel):
    target: str = Field(min_length=1, max_length=32)


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


class FindingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    body: str | None = Field(default=None, max_length=20000)
    status: str | None = Field(default=None, max_length=32)


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    title: str
    body: str
    status: str
    created_by: uuid.UUID
    created_at: datetime


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

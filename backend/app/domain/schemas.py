"""Case & evidence API schemas (Phase 2)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CaseCreate(BaseModel):
    number: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=10000)


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    title: str
    description: str
    status: str
    owner_id: uuid.UUID
    created_at: datetime


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

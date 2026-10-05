"""Analysis API schemas (Phase 4).

`AnalysisResultRead` makes the mandatory fields structural: score, threshold,
model_version, tier and disclaimer are required, so a response that omitted any
of them would fail validation rather than pass silently (T4.5).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AnalysisRequest(BaseModel):
    module: str = Field(min_length=1, max_length=64)
    evidence_id: uuid.UUID
    params: dict[str, Any] = Field(default_factory=dict)


class AnalysisJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    evidence_id: uuid.UUID
    module: str
    model_version: str
    status: str
    params: dict[str, Any]
    error: str
    unsupported_reason: str
    cached: bool
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AnalysisResultRead(BaseModel):
    """A score is never reported without the bar it was judged against."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    evidence_id: uuid.UUID
    module: str
    score: float
    threshold: float
    model_version: str
    tier: str
    disclaimer: str
    payload: dict[str, Any]
    created_at: datetime


class ModuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    version: str
    tier: str
    threshold: float
    requires_gpu: bool
    accepts: tuple[str, ...]
    model_key: str | None
    available: bool
    unavailable_reason: str = ""

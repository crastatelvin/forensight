"""Agent core types (Phase 5).

The load-bearing type here is `Citation`. The project's entire claim is that its
output is defensible, and that claim lives or dies on whether every assertion
can be traced to a real analyzer result. A score floating free of its evidence,
analyzer, threshold and model version is exactly the "simulation" this platform
was rebuilt to eliminate, so citations are a required field rather than
advisory metadata.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Step(StrEnum):
    """Graph stages. One enum, not a framework, until a second graph exists."""

    INTAKE = "intake"
    PLANNER = "planner"
    TOOL_EXEC = "tool_exec"
    MEMORY = "memory"
    REFLECTOR = "reflector"
    HITL = "hitl"
    REPORT = "report"
    DONE = "done"
    #: Set when the agent declines rather than guessing. A first-class terminal
    #: state so "no conclusion" is a recorded outcome, not an empty response.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


TERMINAL_STEPS = frozenset({Step.DONE, Step.INSUFFICIENT_EVIDENCE})


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    #: Stopped at a human checkpoint. `thread_id` + `pending_action_id` say what
    #: is awaiting a decision.
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    DECLINED = "declined"
    REJECTED = "rejected"
    FAILED = "failed"


class Decision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProvenanceError(ValueError):
    """Raised when a claim cannot be traced to a real analyzer result.

    Never caught and downgraded inside the agent: a run that cannot source its
    claims must fail loudly rather than emit a confident sentence.
    """


@dataclass(frozen=True)
class Citation:
    """One traceable pointer to an analysis result.

    `result_id` is the authority: everything else is denormalised from that row
    so a report can render a citation without a second lookup, while still being
    verifiable against it.
    """

    result_id: str
    evidence_id: str
    analyzer: str
    score: float
    threshold: float
    model_version: str
    tier: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "evidence_id": self.evidence_id,
            "analyzer": self.analyzer,
            "score": self.score,
            "threshold": self.threshold,
            "model_version": self.model_version,
            "tier": self.tier,
        }

    def validate(self) -> None:
        """Reject a citation that cannot mean anything.

        A citation missing an analyzer or version is a label, not a reference;
        accepting one would let a caller manufacture provenance by filling in
        blanks.
        """
        for name in ("result_id", "evidence_id", "analyzer", "model_version"):
            if not str(getattr(self, name)).strip():
                raise ProvenanceError(f"citation field {name!r} is empty")
        # Scores are NOT required to be in 0..1: a module picks the scale its
        # score means (entropy is bits/byte, cosine similarity is 0..1). What
        # matters is that score and threshold are finite and comparable, so that
        # "above threshold" has an answer.
        for name in ("score", "threshold"):
            value = float(getattr(self, name))
            if value != value or value in (float("inf"), float("-inf")):
                raise ProvenanceError(f"citation {name} is not a finite number")
        if float(self.threshold) < 0.0:
            raise ProvenanceError(
                f"citation threshold {self.threshold} is negative; no score could "
                f"ever be compared against it"
            )
        if self.tier not in ("A", "B"):
            raise ProvenanceError(f"unknown evidence tier {self.tier!r}")


@dataclass
class Claim:
    """A statement the agent wants to make, plus what supports it.

    `citations` is required and may not be empty: `ProvenanceGuard` rejects the
    claim otherwise. This is the structural form of the honesty clause.
    """

    text: str
    citations: list[Citation] = field(default_factory=list)

    def validate(self) -> None:
        if not self.text.strip():
            raise ProvenanceError("claim text is empty")
        if not self.citations:
            raise ProvenanceError(
                f"claim {self.text[:60]!r} has no citation; unsourced claims are " f"blocked"
            )
        for citation in self.citations:
            citation.validate()


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def args_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.args, sort_keys=True, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        ).hexdigest()


@dataclass
class ToolResult:
    call_id: str
    name: str
    ok: bool
    output: Any
    result_hash: str = ""
    error: str = ""

    def compute_hash(self) -> str:
        self.result_hash = hashlib.sha256(
            json.dumps(
                {"ok": self.ok, "output": self.output, "error": self.error},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return self.result_hash


@dataclass
class PendingAction:
    """A state-changing action waiting on a human.

    Held separately from the transcript so approving it later applies exactly
    what was shown to the human — the arguments are frozen at pause time, not
    re-derived from whatever the model says next.
    """

    action_id: str
    tool: str
    args: dict[str, Any]
    reason: str
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def args_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.args, sort_keys=True, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        ).hexdigest()


@dataclass
class AgentState:
    """Working memory for one run, serialisable so it can be checkpointed."""

    run_id: str
    case_id: str
    thread_id: str
    step: Step = Step.INTAKE
    status: RunStatus = RunStatus.PENDING
    objective: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    pending: PendingAction | None = None
    report: str = ""
    refusal: str = ""
    error: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the checkpoint table. Explicit beats `asdict`, which
        would happily persist the nested dataclasses as shallow copies."""
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "thread_id": self.thread_id,
            "step": self.step.value,
            "status": self.status.value,
            "objective": self.objective,
            "messages": self.messages,
            "citations": [c.as_dict() for c in self.citations],
            "claims": [
                {"text": c.text, "citations": [c.as_dict() for c in c.citations]}
                for c in self.claims
            ],
            "tool_calls": [
                {"name": t.name, "args": t.args, "call_id": t.call_id} for t in self.tool_calls
            ],
            "tool_results": [
                {
                    "call_id": r.call_id,
                    "name": r.name,
                    "ok": r.ok,
                    "output": r.output,
                    "result_hash": r.result_hash,
                    "error": r.error,
                }
                for r in self.tool_results
            ],
            "pending": (
                {
                    "action_id": self.pending.action_id,
                    "tool": self.pending.tool,
                    "args": self.pending.args,
                    "reason": self.pending.reason,
                    "requested_at": self.pending.requested_at.isoformat(),
                }
                if self.pending
                else None
            ),
            "report": self.report,
            "refusal": self.refusal,
            "error": self.error,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentState:
        pending_raw = data.get("pending")
        pending = (
            PendingAction(
                action_id=pending_raw["action_id"],
                tool=pending_raw["tool"],
                args=pending_raw["args"],
                reason=pending_raw["reason"],
                requested_at=datetime.fromisoformat(pending_raw["requested_at"]),
            )
            if pending_raw
            else None
        )
        return cls(
            run_id=data["run_id"],
            case_id=data["case_id"],
            thread_id=data["thread_id"],
            step=Step(data["step"]),
            status=RunStatus(data["status"]),
            objective=data.get("objective", ""),
            messages=data.get("messages", []),
            citations=[Citation(**c) for c in data.get("citations", [])],
            claims=[
                Claim(text=c["text"], citations=[Citation(**x) for x in c["citations"]])
                for c in data.get("claims", [])
            ],
            tool_calls=[ToolCall(**t) for t in data.get("tool_calls", [])],
            tool_results=[ToolResult(**r) for r in data.get("tool_results", [])],
            pending=pending,
            report=data.get("report", ""),
            refusal=data.get("refusal", ""),
            error=data.get("error", ""),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )

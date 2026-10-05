"""Case lifecycle state machine (T2.7).

Only transitions in CASE_TRANSITIONS are allowed. Illegal transitions raise
IllegalTransitionError so the API can return 409.
"""

from __future__ import annotations

from app.domain.models import CaseStatus

CASE_TRANSITIONS: dict[str, frozenset[str]] = {
    CaseStatus.INTAKE: frozenset({CaseStatus.OPEN, CaseStatus.ARCHIVED}),
    CaseStatus.OPEN: frozenset({CaseStatus.INVESTIGATING, CaseStatus.ARCHIVED}),
    CaseStatus.INVESTIGATING: frozenset({CaseStatus.FINDINGS, CaseStatus.ARCHIVED}),
    CaseStatus.FINDINGS: frozenset(
        {CaseStatus.REVIEW, CaseStatus.INVESTIGATING, CaseStatus.ARCHIVED}
    ),
    CaseStatus.REVIEW: frozenset(
        {CaseStatus.REPORTED, CaseStatus.INVESTIGATING, CaseStatus.ARCHIVED}
    ),
    CaseStatus.REPORTED: frozenset({CaseStatus.ARCHIVED}),
    CaseStatus.ARCHIVED: frozenset(),  # terminal
}


class IllegalTransitionError(ValueError):
    """Raised when a case status transition is not permitted."""


def can_transition(current: str, target: str) -> bool:
    return target in CASE_TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise IllegalTransitionError(f"Illegal case transition: {current!r} -> {target!r}")

"""Case-level access control (T3.1, T3.7).

Central authorization for case-scoped resources, enforced in one place rather
than copy-pasted per route. RBAC (route-level role gates) decides *who may call
an endpoint*; this module decides *which cases a caller may see or change*.

Access model
------------
- ADMIN:    full read + write on every case (organisation-wide authority).
- OFFICER:  read + write only on cases they own (owner_id) or assigned to them.
- EXPERT:   read + write only on cases assigned to them (assigned_to).
- REVIEWER: read-only oversight across every case; never write.

A caller who lacks read access to a specific case receives 404 (not 403) so the
API does not disclose the existence of cases outside their scope.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status

from app.auth.rbac import CurrentUser
from app.auth.roles import Role
from app.domain.models import Case


class CaseAccessError(HTTPException):
    """Raised when a caller may not access a case.

    Uses 404 for read-scope misses (non-disclosure) and 403 for a write
    attempt on a case the caller may read but not modify.
    """

    def __init__(self, *, disclose: bool) -> None:
        if disclose:
            super().__init__(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify this case",
            )
        else:
            super().__init__(
                status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
            )


def _is_owner_or_assignee(case: Case, user_id: uuid.UUID) -> bool:
    return case.owner_id == user_id or case.assigned_to == user_id


def can_read_case(case: Case, user: CurrentUser) -> bool:
    user_id = uuid.UUID(user.id)
    if user.role in (Role.ADMIN, Role.REVIEWER):
        return True
    if user.role in (Role.OFFICER, Role.EXPERT):
        return _is_owner_or_assignee(case, user_id)
    return False


def can_write_case(case: Case, user: CurrentUser) -> bool:
    user_id = uuid.UUID(user.id)
    if user.role == Role.ADMIN:
        return True
    if user.role == Role.REVIEWER:
        return False  # read-only oversight
    if user.role in (Role.OFFICER, Role.EXPERT):
        return _is_owner_or_assignee(case, user_id)
    return False


def assert_can_read(case: Case, user: CurrentUser) -> None:
    if not can_read_case(case, user):
        raise CaseAccessError(disclose=False)


def assert_can_write(case: Case, user: CurrentUser) -> None:
    # A caller who cannot even read the case gets a non-disclosing 404; a
    # caller who may read but not write (e.g. reviewer) gets an explicit 403.
    if not can_read_case(case, user):
        raise CaseAccessError(disclose=False)
    if not can_write_case(case, user):
        raise CaseAccessError(disclose=True)

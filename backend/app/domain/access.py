"""Case-level access control (T3.1, T3.7).

Central authorization for case-scoped resources, enforced in one place rather
than copy-pasted per route. RBAC (route-level role gates) decides *who may call
an endpoint*; this module decides *which cases a caller may see or change*.

Access model
------------
- ADMIN:    full read + write on every case (organisation-wide authority).
- OFFICER:  read + write on cases they own or are assigned to.
- EXPERT:   read + write on cases they own or are assigned to.
- REVIEWER: read-only oversight across every case; never write.

Note EXPERT matches OFFICERT: both roles are in `MUTATING_ROLES`, so either can
create a case and become its owner. Scoping experts to assigned-only would lock
them out of the cases they just opened.

A caller who lacks read access to a case receives 404 (not 403) so the API does
not disclose the existence of cases outside their scope.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.auth.rbac import CurrentUser
from app.auth.roles import Role
from app.domain.models import Case

# Roles that see every case regardless of ownership.
GLOBAL_READERS: frozenset[Role] = frozenset({Role.ADMIN, Role.REVIEWER})


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
            super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")


def _is_owner_or_assignee(case: Case, user_id: uuid.UUID) -> bool:
    return case.owner_id == user_id or case.assigned_to == user_id


def can_read_case(case: Case, user: CurrentUser) -> bool:
    if user.role in GLOBAL_READERS:
        return True
    if user.role in (Role.OFFICER, Role.EXPERT):
        return _is_owner_or_assignee(case, uuid.UUID(user.id))
    return False


def can_write_case(case: Case, user: CurrentUser) -> bool:
    if user.role == Role.ADMIN:
        return True
    if user.role == Role.REVIEWER:
        return False  # read-only oversight
    if user.role in (Role.OFFICER, Role.EXPERT):
        return _is_owner_or_assignee(case, uuid.UUID(user.id))
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


async def scoped_case(session: AsyncSession, case_id: uuid.UUID, user: CurrentUser) -> Case:
    """Load a case the user may see, or 404.

    Every case-scoped route goes through this. It lived as a private copy in three
    routers, which is three places for an authorisation rule to be edited and one of
    them to be missed — and the copy that gets missed is the one that leaks. They had
    not drifted yet; the argument against them is that they are waiting to.

    A case that exists but belongs to someone else is reported as **404, not 403**: a
    403 would confirm the id is real to a user with no right to know that it is.
    """
    case = await visible_case(session, case_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def visible_case(session: AsyncSession, case_id: uuid.UUID, user: CurrentUser) -> Case | None:
    """Fetch a case only if `user` may see it. None covers both 404 and 403.

    Returning None for "exists but forbidden" is deliberate: a 403 would confirm
    that the id exists to a user with no right to know that.
    """
    case = await session.get(Case, case_id)
    if case is None or not can_read_case(case, user):
        return None
    return case


def case_scope_filter(user: CurrentUser) -> ColumnElement[bool] | None:
    """SQL predicate narrowing a Case SELECT to what `user` may see.

    Returns None when the role sees everything, so callers apply the predicate
    conditionally instead of building two different queries.
    """
    if user.role in GLOBAL_READERS:
        return None
    uid = uuid.UUID(user.id)
    return or_(Case.owner_id == uid, Case.assigned_to == uid)

"""User roles for RBAC.

Four roles per the plan (Decision 5, 6 Security):
- ADMIN: full access, user management.
- OFFICER: investigator; owns and works cases.
- EXPERT: forensic expert; runs analyses, records findings.
- REVIEWER: read-only oversight.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OFFICER = "officer"
    EXPERT = "expert"
    REVIEWER = "reviewer"


# Roles allowed to perform state-changing (mutating) operations by default.
MUTATING_ROLES: frozenset[Role] = frozenset({Role.ADMIN, Role.OFFICER, Role.EXPERT})

# Every valid role.
ALL_ROLES: frozenset[Role] = frozenset(Role)

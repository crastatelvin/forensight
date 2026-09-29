"""Seed the initial admin user (T1.7).

Requires SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD from the environment. There
are NO default/hardcoded credentials — the script refuses to run without them.
Idempotent: if the admin already exists, it does nothing.

Usage:
    SEED_ADMIN_EMAIL=admin@example.com SEED_ADMIN_PASSWORD=... \
        uv run python -m app.seed
"""
from __future__ import annotations

import asyncio
import sys

from app.auth.repository import create_user, get_user_by_email
from app.auth.roles import Role
from app.config import get_settings
from app.db import SessionLocal


async def seed_admin() -> int:
    settings = get_settings()
    email = settings.seed_admin_email.strip()
    password = settings.seed_admin_password

    if not email or not password:
        print(
            "ERROR: SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD must be set. "
            "No default credentials are provided.",
            file=sys.stderr,
        )
        return 2
    if len(password) < 12:
        print("ERROR: SEED_ADMIN_PASSWORD must be at least 12 characters.", file=sys.stderr)
        return 2

    async with SessionLocal() as session:
        if await get_user_by_email(session, email) is not None:
            print(f"Admin '{email}' already exists; nothing to do.")
            return 0
        await create_user(session, email=email, password=password, role=Role.ADMIN)
        await session.commit()
        print(f"Created admin user '{email}'.")
        return 0


def main() -> None:
    raise SystemExit(asyncio.run(seed_admin()))


if __name__ == "__main__":
    main()

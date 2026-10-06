#!/usr/bin/env python3
"""Dependency audit gate (CI `backend` job).

Why this exists rather than a bare `pip-audit`:

- `pip-audit --strict` against the environment fails on *this project itself*,
  because it is installed editable and is not on PyPI. There is no flag to skip
  one named package (`--skip` takes a project path, not a name).
- So the direct requirements are extracted from `pyproject.toml` into a
  requirements file and audited from there. The local package is excluded by
  construction and every third-party dependency is still checked.

This was worth writing rather than papering over with `|| true`. The advisory
`|| true` stood in for is gone -- the CVE backlog is clear -- and a security gate
that prints green while checking nothing is worse than no gate at all.

Exits non-zero on any known vulnerability, so CI fails.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

#: The project itself. Installed editable, not published, and therefore not
#: something pip-audit can resolve. Excluding it here is the whole point.
LOCAL_PACKAGE = "forensight-backend"


def _package_name(requirement: str) -> str:
    """The distribution name from a PEP 508 requirement string."""
    head = requirement.split(";")[0].strip()
    for separator in ("==", ">=", "<=", "~=", "!=", ">", "<", "["):
        head = head.split(separator)[0]
    return head.strip().lower()


def requirements() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    declared = list(data["project"]["dependencies"])
    declared += list(data.get("dependency-groups", {}).get("dev", []))
    return [r for r in declared if _package_name(r) != LOCAL_PACKAGE]


def main() -> int:
    reqs = requirements()
    if not reqs:
        print("no requirements found; refusing to report a clean audit", file=sys.stderr)
        return 1

    target = ROOT / ".audit-requirements.txt"
    target.write_text("\n".join(reqs) + "\n")
    print(f"auditing {len(reqs)} direct dependencies (excluding {LOCAL_PACKAGE})")
    try:
        # No shell, and every argument is either a literal or a path this script
        # just wrote. The requirement *contents* go into the file rather than onto
        # the command line, so nothing derived from pyproject.toml is ever
        # interpreted by a shell.
        return subprocess.call(  # noqa: S603 - fixed argv, shell=False
            [sys.executable, "-m", "pip_audit", "--strict", "-r", str(target)],
            shell=False,
        )
    finally:
        target.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Prove the security gates fail on the things they claim to catch.

Every scanner in CI is configured and green. That is not evidence any of them work:
a scanner with a broken rule path, an empty include glob, or a typo'd binary exits 0
and reads in the CI UI exactly like a scanner that found nothing. The difference is
only visible when it is shown a real problem.

So this plants one of each and asserts the scanner rejects it:

| Gate | Planted |
|---|---|
| gitleaks | an AWS-shaped key in a source file |
| bandit | `md5` used for a password, and a hardcoded password |
| Semgrep | `subprocess` with `shell=True` |
| pip-audit | a requirement pinned to a known-vulnerable release |

Everything is written to a temporary directory that is deleted afterwards, so
nothing here can end up in the repository or in a scan of it. The file paths handed
to each scanner point inside that directory, so the scanners analyse the planted
material and nothing else.

Exits 0 only if every gate rejected its planted problem. **A gate that cannot be
made to fail is reported as a failure**, not skipped — "the scanner said nothing" is
the outcome this exists to detect, and passing it quietly would defeat the point.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Outcome:
    gate: str
    detected: bool
    detail: str
    available: bool = True


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    """Run a scanner, returning its exit code and combined output."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, our own temp dir
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except FileNotFoundError:
        return -1, "scanner not installed"
    except subprocess.TimeoutExpired:
        return -2, "scanner timed out"
    return proc.returncode, (proc.stdout + proc.stderr)


def _plant(directory: Path) -> None:
    """Write the deliberately vulnerable material."""
    (directory / "app").mkdir(parents=True, exist_ok=True)

    # gitleaks: an AWS access key id. Shape-recognised, never valid.
    (directory / "app" / "config.py").write_text(
        'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'
        'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
    )

    # bandit: md5 for a password, plus a hardcoded one. Both are B324/B105.
    (directory / "app" / "auth.py").write_text(
        "import hashlib\n"
        "\n"
        'PASSWORD = "hunter2-correct-horse"\n'
        "\n"
        "def check(password: str, stored: str) -> bool:\n"
        "    # MD5 for passwords is B324.\n"
        "    return hashlib.md5(password.encode()).hexdigest() == stored\n"
    )

    # Semgrep: shell=True with an interpolated value.
    (directory / "app" / "runner.py").write_text(
        "import subprocess\n"
        "\n"
        "\n"
        "def run(target: str) -> None:\n"
        '    subprocess.run(f"tool --target {target}", shell=True, check=True)\n'
    )


def check_gitleaks(directory: Path) -> Outcome:
    if shutil.which("gitleaks") is None:
        return Outcome("gitleaks", False, "not installed", available=False)
    code, out = _run(["gitleaks", "detect", "--no-git", "--source", str(directory)], directory)
    detected = code != 0 and "AKIA" in out
    return Outcome("gitleaks", detected, f"exit={code}: {out.strip()[:160]}")


def check_bandit(directory: Path) -> Outcome:
    if shutil.which("bandit") is None:
        return Outcome("bandit", False, "not installed", available=False)
    code, out = _run(["bandit", "-r", str(directory / "app"), "-ll"], directory)
    detected = code != 0 and ("B105" in out or "B324" in out)
    return Outcome("bandit", detected, f"exit={code}: {out.strip()[:160]}")


def check_semgrep(directory: Path) -> Outcome:
    if shutil.which("semgrep") is None:
        return Outcome("semgrep", False, "not installed", available=False)
    code, out = _run(
        [
            "semgrep",
            "scan",
            "--config",
            "p/python",
            "--quiet",
            "--disable-version-check",
            "--metrics=off",
            str(directory / "app"),
        ],
        directory,
    )
    detected = code != 0
    return Outcome("semgrep", detected, f"exit={code}: {out.strip()[:160]}")


def check_pip_audit(directory: Path) -> Outcome:
    """A requirement pinned to a release with a published advisory.

    Uses `requirements.txt` rather than installing anything: `pip-audit -r` audits a
    declared set without resolving it into this environment, so the check cannot
    disturb the tree it is checking.
    """
    if shutil.which("pip-audit") is None:
        return Outcome("pip-audit", False, "not installed", available=False)
    requirements = directory / "requirements.txt"
    # setuptools 65.5.0 carries several published advisories; it is old, stable, and
    # nowhere near anything this project depends on.
    requirements.write_text("setuptools==65.5.0\n")
    code, out = _run(["pip-audit", "-r", str(requirements), "--progress-spinner", "off"], directory)
    detected = code != 0 and "setuptools" in out
    return Outcome("pip-audit", detected, f"exit={code}: {out.strip()[:160]}")


CHECKS = (check_gitleaks, check_bandit, check_semgrep, check_pip_audit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="report unavailable scanners as skipped rather than failed. Only "
        "appropriate locally; CI must run them all.",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="forensight-gate-check-") as tmp:
        directory = Path(tmp)
        _plant(directory)

        outcomes = [check(directory) for check in CHECKS]

    width = max(len(o.gate) for o in outcomes)
    skipped = [o for o in outcomes if not o.available]
    failures = 0
    for outcome in outcomes:
        if not outcome.available:
            status = "SKIP" if args.allow_missing else "FAIL"
            if not args.allow_missing:
                failures += 1
            print(f"{outcome.gate:<{width}}  {status}  {outcome.detail}")
        elif outcome.detected:
            print(f"{outcome.gate:<{width}}  OK    rejected the planted problem")
        else:
            failures += 1
            print(f"{outcome.gate:<{width}}  FAIL  did not catch it: {outcome.detail}")

    if failures:
        print(
            f"\n{failures} gate(s) did not behave as advertised. A scanner that "
            f"passes everything is indistinguishable from one that is checking "
            f"nothing, so this is a failure either way."
        )
        return 1

    if skipped:
        # The summary has to say what it does not know. An earlier version printed
        # "every configured gate rejected the problem planted for it" even when every
        # gate had been skipped for want of a binary — which is the exact false
        # assurance this script exists to prevent, committed in the script itself.
        print(
            f"\n{len(outcomes) - len(skipped)} of {len(outcomes)} gates rejected the "
            f"problem planted for them. Not verified: "
            f"{', '.join(o.gate for o in skipped)}."
        )
    else:
        print("\nEvery configured gate rejected the problem planted for it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

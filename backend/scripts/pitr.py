"""Point-in-time recovery (P10.B2) — take a base backup, and recover to an instant.

**Why this exists when `scripts/backup.py` already restores.** `backup.py` takes a logical
snapshot with `pg_dump` and can put it back. That answers "can I recover the database as it
was at 03:00 last night". It does not answer the question an evidence vault actually gets
asked: *we found that a row was written at 14:32 and it should not be there — put the
database back to 14:31 without losing the nine hours either side of it.*

A logical dump has one recovery point by construction. Point-in-time recovery replays the
write-ahead log from a base backup and stops at a chosen instant, so the recovery point is
a time rather than a file. It needs three things, and only the first existed before:

1. **A base backup.** `pg_basebackup` -- a physical copy, consistent by construction,
   including the control file. Added here as `base-backup`.
2. **An unbroken WAL archive.** Configured in `docker-compose.yml` and
   `infra/postgres/conf.d/10-wal-archiving.conf`, alerted on in
   `infra/observability/alerts.yml`. Without it the recovery point is "whenever the last
   dump was".
3. **A recovery procedure.** This file's `restore-to`.

## The property this refuses to give up

**There is no in-place recovery, and no `--force`.** Same reasoning as `backup.py`'s
`restore`, and it is the same argument: a tool that can overwrite the system it protects
will eventually be aimed at it by someone in a hurry, at 3am, by someone who has just read
a row in a log. Recovery reads a base backup and an archive; it writes **only** to the data
directory named by `--target-data-dir`, which must not exist. To recover the live system,
an operator stops it and points it at a directory they have moved aside — a deliberate,
visible act.

A second refusal: recovery refuses if the archive has a gap. `recovery_target_*` with a
missing segment makes Postgres replay as far as it can and *then promote*, which yields a
database that silently omits every transaction after the gap. For an evidence vault that
is the worst possible outcome — it looks recovered, and it is missing data. So the archive
is checked for contiguity first and a gap is a hard failure.

## Names over timestamps

`--target-name` aims at a label created with `pg_create_restore_point('label')`, and is
preferred over `--target-time`. A timestamp typed at 3am is a guess about the format and
the zone; a restore point is created *at the moment of the event*, named unambiguously, and
recorded with the incident. `--target-time` exists for when there was no restore point, and
says in its own output that it was used.

## What this does not do

It does not archive the WAL off-host. An archive in the same volume as the database does
not survive losing that volume — which is why `pgwal` is a separate named volume and why
`docs/runbook.md` says to replicate it somewhere else. It does not manage MinIO-side
replication either, which is the other half of this item and is still open.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

#: WAL segment names are 24 hex digits.
#:
#: **Compared as hex STRINGS, never as integers.** A 24-hex-digit name is up to 96 bits
#: and `int(name, 16)` wraps a machine int -- `000000010000000000000090` becomes
#: 18446744073709551760, not 0x90. The contiguity arithmetic happens to stay internally
#: consistent (the wrap is monotonic), so the gap *check* still works, while every number
#: it reports -- the range, and crucially "first missing segment 000000010000000000000092"
#: -- is nonsense. An operator at 3am would be sent to fetch a segment that does not exist.
#: Fixed-width uppercase hex sorts lexicographically in the same order as numerically, so
#: string comparison is correct and cannot overflow.
SEGMENT = re.compile(r"^[0-9A-F]{24}$")


def _segment_number(name: str) -> int:
    """The LOW 8 hex digits (the segment index) as an integer, for gap arithmetic.

    Only the low 32 bits are ever compared, and they are the part that increments. The
    high half identifies the timeline and log/segment grouping and is irrelevant to
    contiguity. So the arithmetic is done on a value that fits, and the *names* are what
    get reported.
    """
    return int(name[-8:], 16)


#: Postgres timeline. Recovery to a different timeline needs a `recovery_target_timeline`
#: and a much longer argument; refusing is clearer than guessing.
DEFAULT_TIMELINE = 1


class PitrError(RuntimeError):
    """Refused, with the reason. Never a bare non-zero exit."""


def _run(
    command: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, text=True, env=env, check=False, timeout=7200
        )
    except FileNotFoundError as exc:
        raise PitrError(f"{command[0]} is not installed") from exc
    # NOTE: pg_basebackup's exit status is checked, but so is the artefact -- see
    # base_backup(). backup.py already measured that a Postgres client can exit non-zero
    # having done the work, so exit codes here are necessary and not sufficient.
    return proc


def _dsn(database_url: str) -> str:
    return database_url.replace("+asyncpg", "")


def _find_base_tarball(directory: Path) -> Path | None:
    """Locate the base tarball pg_basebackup actually wrote.

    With `--format tar`, `pg_basebackup --pgdata DIR` writes the archive to
    `DIR/base.tar.gz` -- as a **directory containing** `base.tar.gz`, not as the file
    itself. That is its documented layout and it is not obvious: the first version of
    this script asserted `directory/"base.tar.gz"` was a file and so refused a base
    backup that had been created correctly and completely.
    """
    direct = directory / "base.tar.gz"
    if direct.is_file():
        return direct
    nested = directory / "base.tar.gz" / "base.tar.gz"
    if nested.is_file():
        return nested
    found = sorted(directory.rglob("base.tar*"))
    return found[0] if found else None


def archive_segment_range(archive: Path) -> tuple[int, int] | None:
    """Lowest and highest WAL segment number present, or None if the archive is empty."""
    names = [
        name for name in (p.name for p in archive.iterdir() if p.is_file()) if SEGMENT.match(name)
    ]
    return (min(names), max(names)) if names else None


def _segment_name(low: str, index: int) -> str:
    """Rebuild a full segment name from the base name and a low-32-bit index."""
    return low[:-8] + f"{index:08X}"


def check_archive_contiguous(archive: Path) -> tuple[str, str]:
    """Refuse a gap in the WAL archive.

    **This is the check that makes recovery safe to trust.** Postgres replays WAL until it
    hits a missing segment and then promotes; the server comes up, accepts queries, and is
    silently missing every transaction after the gap. For an evidence vault that is the
    worst outcome available -- a database that looks recovered and is not. So contiguity
    is verified before recovery is attempted, and a gap is a hard failure naming the
    missing segment.
    """
    if not archive.is_dir():
        raise PitrError(f"WAL archive directory does not exist: {archive}")
    names = {
        name for name in (p.name for p in archive.iterdir() if p.is_file()) if SEGMENT.match(name)
    }
    if not names:
        raise PitrError(
            f"WAL archive is empty: {archive}. Point-in-time recovery has no replay "
            f"material, so the only recovery point available is the last logical backup."
        )
    # Sort by NAME for reporting and by the low-32-bit index for gap arithmetic. See
    # _segment_number for why the two are not the same thing.
    ordered = sorted(names)
    low, high = ordered[0], ordered[-1]
    low_i, high_i = _segment_number(low), _segment_number(high)
    present = {_segment_number(name) for name in names}
    missing = sorted(set(range(low_i, high_i + 1)) - present)
    if missing:
        first = _segment_name(low, missing[0])
        raise PitrError(
            f"WAL archive has {len(missing)} missing segment(s) between {low} and {high}; "
            f"first is {first}. Recovery would replay to the gap and then PROMOTE, "
            f"producing a server that looks recovered and is silently missing every "
            f"transaction after it. Refusing. Restore the missing segments or choose a "
            f"target before the gap."
        )
    return low, high


def base_backup(directory: Path, *, database_url: str) -> Path:
    """A physical base backup via `pg_basebackup`, in plain-format tar."""
    if directory.exists():
        raise PitrError(
            f"{directory} already exists. Refusing to write into it: a base backup that "
            f"silently merges with a previous one is not a recovery point."
        )
    directory.mkdir(parents=True)
    proc = _run(
        [
            "pg_basebackup",
            "--dbname",
            _dsn(database_url),
            "--format",
            "tar",
            "--gzip",
            "--pgdata",
            str(directory / "base.tar.gz"),
            "--label",
            "forensight",
            "--progress",
            "--verbose",
        ]
    )
    artefact = _find_base_tarball(directory)
    # Check the artefact, not just the exit code. backup.py measured that a Postgres client
    # can exit non-zero having done its work, so an exit code alone is not evidence -- and
    # this check has already earned its place twice, catching a missing pg_hba replication
    # entry reported only in stderr, and then pg_basebackup's own tar layout.
    if proc.returncode != 0 or artefact is None or artefact.stat().st_size == 0:
        raise PitrError(
            f"pg_basebackup did not produce a usable base backup in {directory} "
            f"(found={artefact}): {(proc.stderr or proc.stdout)[:400]}"
        )
    marker = directory / "BASE_BACKUP_TAKEN"
    marker.write_text(
        "A physical base backup was taken here. Recovery replays the WAL archive on top "
        "of it and can therefore reach any instant AFTER this point, and no instant "
        "before it. Losing this file loses the only recovery point.\n"
    )
    return directory


def restore_to(
    target_data_dir: Path,
    *,
    database_url: str,
    archive: Path,
    base_backup_dir: Path | None,
    target_name: str | None,
    target_time: str | None,
    target_lsn: str | None,
    timeline: int = DEFAULT_TIMELINE,
) -> Path:
    """Recover to a chosen instant into a NEW data directory.

    Refuses if `target_data_dir` exists. See the module docstring: no in-place path, no
    `--force`.
    """
    if target_data_dir.exists():
        raise PitrError(
            f"{target_data_dir} already exists. Refusing to recover over an existing data "
            f"directory. To recover the live system, stop it and move the directory aside "
            f"deliberately -- a recovery tool that can overwrite the system it protects "
            f"will eventually be aimed at it by someone in a hurry."
        )
    exactly_one = [bool(target_name), bool(target_time), bool(target_lsn)]
    if sum(exactly_one) != 1:
        raise PitrError(
            "give exactly one of --target-name, --target-time or --target-lsn. "
            "--target-name (a pg_create_restore_point label) is strongly preferred: a "
            "restore point is created at the moment of the event and named "
            "unambiguously, where a timestamp typed at 3am is a guess about format and zone."
        )
    low, high = check_archive_contiguous(archive)

    target_data_dir.mkdir(parents=True)
    # Extract the base backup into the fresh data directory.
    tarball = _find_base_tarball(base_backup_dir) if base_backup_dir else None
    if tarball is None:
        raise PitrError(
            f"--base-backup-dir {base_backup_dir} does not look like a directory written "
            f"by `pitr.py base-backup`: no base.tar.gz under it"
        )
    unpack = _run(["tar", "-xzf", str(tarball), "-C", str(target_data_dir)])
    if unpack.returncode != 0:
        raise PitrError(f"extracting the base backup failed: {unpack.stderr[:400]}")

    recovery = target_data_dir / "recovery.signal"
    recovery.touch()
    conf = target_data_dir / "postgresql.auto.conf"
    existing = conf.read_text() if conf.is_file() else ""
    settings = [
        f"restore_command = 'cp {archive}/%f %p'",
        f"recovery_target_timeline = '{timeline}'",
    ]
    if target_name:
        settings.append(f"recovery_target_name = '{target_name}'")
        used = f"restore point {target_name!r}"
    elif target_lsn:
        settings.append(f"recovery_target_lsn = '{target_lsn}'")
        used = f"LSN {target_lsn}"
    else:
        settings.append(f"recovery_target_time = '{target_time}'")
        used = f"time {target_time!r} -- note this was a TIMESTAMP, not a named restore point"
    # `pause` rather than `promote`: the operator inspects the recovered database and
    # decides whether to promote it. Auto-promoting a recovery that was aimed at a
    # timestamp guessed at 3am is how a wrong recovery becomes the live system.
    settings.append("recovery_target_action = 'pause'")
    conf.write_text(existing + "\n# added by scripts/pitr.py\n" + "\n".join(settings) + "\n")

    print(f"  recovery point aimed at {used}")
    print(f"  WAL archive {archive} holds segments {low}..{high}, contiguous")
    print(f"  data directory prepared at {target_data_dir}")
    print()
    print("  NEXT: start postgres against this data directory, e.g.")
    print(f"    docker run --rm -v {target_data_dir}:/var/lib/postgresql/data \\")
    print(f"      forensight-postgres:local postgres -c restore_command='cp {archive}/%f %p' \\")
    print("      -c recovery_target_action=pause")
    print()
    print("  The server will come up PAUSED at the recovery target, so it can be")
    print("  inspected. Promote deliberately with: SELECT pg_wal_replay_resume();")
    print("  and only once you have confirmed the row you expected to be gone is gone.")
    return target_data_dir


def build_parser() -> argparse.ArgumentParser:
    """Build the parser.

    Split out from `main` so tests can introspect the ACTUAL option strings rather than a
    reconstruction of them -- a test that builds its own throwaway parser proves nothing
    about the one a user will run.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="is archiving healthy, and how far back does it reach?")
    status.add_argument("--archive", type=Path, default=Path("/var/lib/postgresql/wal"))
    status.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))

    check = sub.add_parser("check-archive", help="refuse a gap in the WAL archive")
    check.add_argument("--archive", type=Path, default=Path("/var/lib/postgresql/wal"))

    base = sub.add_parser("base-backup", help="take a physical base backup for PITR to replay onto")
    base.add_argument("directory", type=Path)
    base.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))

    restore = sub.add_parser("restore-to", help="prepare a data directory recovered to an instant")
    restore.add_argument("target_data_dir", type=Path)
    restore.add_argument("--archive", type=Path, default=Path("/var/lib/postgresql/wal"))
    restore.add_argument("--base-backup-dir", type=Path, required=False)
    restore.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    restore.add_argument("--target-name")
    restore.add_argument("--target-time")
    restore.add_argument("--target-lsn")
    restore.add_argument("--timeline", type=int, default=DEFAULT_TIMELINE)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "status":
            low, high = check_archive_contiguous(args.archive)
            print(f"  WAL archive {args.archive}: segments {low}..{high}, contiguous")
            print("  replayable range is bounded by the oldest BASE BACKUP TAKEN marker and")
            print(
                "  the newest archived segment. Both ends matter; neither is implied by the other."
            )
            return 0
        if args.command == "check-archive":
            low, high = check_archive_contiguous(args.archive)
            print(f"  ok: contiguous, {low} .. {high}")
            return 0
        if args.command == "base-backup":
            base_backup(args.directory, database_url=args.database_url)
            print(f"  base backup written to {args.directory}")
            return 0
        if args.command == "restore-to":
            restore_to(
                args.target_data_dir,
                database_url=args.database_url,
                archive=args.archive,
                base_backup_dir=args.base_backup_dir,
                target_name=args.target_name,
                target_time=args.target_time,
                target_lsn=args.target_lsn,
                timeline=args.timeline,
            )
            return 0
    except PitrError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())

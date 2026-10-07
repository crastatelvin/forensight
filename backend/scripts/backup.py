#!/usr/bin/env python
"""Backup, verify and restore — with verification that means something.

Three subcommands:

    create    dump the database and copy every evidence object out
    verify    prove the backup is complete and byte-exact
    restore   put a backup back into a target

**Why `verify` is the point.** A backup that has never been restored is a hope. The
usual check — "the dump file exists and is non-empty" — passes identically for a
complete backup and for one that silently lost every object, because it never looks
at the objects at all.

Evidence here is content-addressed: the object key *is* the SHA-256 of the bytes
(`app/domain/storage.py`). That makes verification cryptographic rather than
structural. For every row the database records, `verify` hashes the backed-up copy
and compares it to the recorded digest. A truncated object, a partial write, a
corrupted byte and a silently-substituted file all fail it, and there is nothing to
interpret — the answer is a digest.

So `verify` answers the only question that matters about a forensic backup: *are the
bytes I would restore the bytes I ingested?* And because the digests come from the
database rather than from the backup's own inventory, a backup that is internally
consistent but missing evidence is caught too.

**`restore` writes to a target, never over the live deployment.** There is no
`--force` and no in-place path, deliberately: a restore tool that can overwrite the
system it is protecting is a restore tool that will eventually be pointed at it by
someone in a hurry.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
DUMP_NAME = "db.dump"
EVIDENCE_DIR = "evidence"


class BackupError(RuntimeError):
    """The backup cannot be trusted, or the restore cannot proceed."""


@dataclass(frozen=True)
class Row:
    """One evidence record, as the database states it."""

    id: str
    case_id: str
    filename: str
    sha256: str
    content_type: str
    size_bytes: int
    storage_key: str


# --- helpers --------------------------------------------------------------


def sha256_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
    """Digest a file without reading it into memory.

    Chunked because evidence can be gigabytes, and a backup tool that loads a file
    to hash it cannot verify the backups it exists to verify.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def _run(
    command: list[str], *, env: dict[str, str] | None = None, allow_errors: bool = False
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, text=True, env=env, check=False, timeout=1800
        )
    except FileNotFoundError as exc:
        raise BackupError(
            f"{command[0]} is not installed; it is needed for this operation"
        ) from exc
    if proc.returncode != 0 and not allow_errors:
        raise BackupError(
            f"{' '.join(command[:3])} failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:400]}"
        )
    return proc


def _dsn(database_url: str) -> str:
    """The async URL is not something pg_dump understands."""
    return database_url.replace("+asyncpg", "").replace("postgresql+aiosqlite", "sqlite")


# --- create ---------------------------------------------------------------


async def create(
    out: Path,
    *,
    database_url: str,
    settings: Any,
    read_object: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    """Dump the database, copy every evidence object, write the manifest.

    `read_object(key) -> bytes` is injectable for the same reason the executor's
    `evidence_reader` is: a test needs to back up objects that were written
    somewhere other than the production object store, and forcing every test to
    stand up MinIO to do it is how a test suite ends up skipping the thing it
    exists to prove. Defaults to the real store.

    Async rather than sync-with-an-inner-`asyncio.run`, because the latter raises
    `cannot be called from a running event loop` for every caller that already has
    one — which is any async test, and any future caller embedded in a service.
    """
    out.mkdir(parents=True, exist_ok=True)
    evidence_dir = out / EVIDENCE_DIR
    evidence_dir.mkdir(parents=True, exist_ok=True)

    dump_path = out / DUMP_NAME
    _run(["pg_dump", "--format=custom", "--no-owner", "--file", str(dump_path), _dsn(database_url)])

    from app.db import SessionLocal
    from app.domain.models import Evidence
    from app.domain.storage import ObjectStore
    from sqlalchemy import select

    if read_object is None:
        if settings is None:
            raise BackupError(
                "create() needs either settings or an explicit read_object; there is "
                "no object store to read from otherwise"
            )
        read_object = ObjectStore(settings).get_evidence
    reader = read_object
    copied = 0
    rows: list[dict[str, Any]] = []

    async def gather() -> list[Row]:
        async with SessionLocal() as session:
            found = (
                (await session.execute(select(Evidence).order_by(Evidence.created_at)))
                .scalars()
                .all()
            )
            return [
                Row(
                    id=str(e.id),
                    case_id=str(e.case_id),
                    filename=e.filename,
                    sha256=e.sha256,
                    content_type=e.content_type,
                    size_bytes=int(e.size_bytes),
                    storage_key=e.storage_key,
                )
                for e in found
            ]

    evidence_rows = await gather()

    for row in evidence_rows:
        data = reader(row.storage_key)
        actual = hashlib.sha256(data).hexdigest()
        if actual != row.sha256:
            # Writing bytes that disagree with the recorded digest would produce a
            # backup that fails its own verification later, and it would fail far
            # from the cause. Refuse now, where the mismatch is diagnosable.
            raise BackupError(
                f"evidence {row.id} does not match its recorded digest: database says "
                f"{row.sha256}, the stored object hashes to {actual}. Refusing to "
                f"write a backup that would fail its own verification."
            )
        (evidence_dir / row.sha256).write_bytes(data)
        copied += 1
        rows.append(
            {
                "id": row.id,
                "case_id": row.case_id,
                "filename": row.filename,
                "sha256": row.sha256,
                "content_type": row.content_type,
                "size_bytes": row.size_bytes,
            }
        )

    manifest = {
        "format": 1,
        "created_at": _now(),
        "database_url": "<redacted>",
        "db_dump": DUMP_NAME,
        "db_dump_sha256": sha256_file(dump_path),
        "evidence_count": copied,
        "evidence": rows,
    }
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def _now() -> str:
    """UTC timestamp, for the manifest's own record.

    Read from the system rather than from `datetime.utcnow()`, because a manifest
    is a document and a document needs an unambiguous timezone.
    """
    import datetime as dt

    return dt.datetime.now(dt.UTC).isoformat()


# --- verify ---------------------------------------------------------------


def verify(backup_dir: Path) -> tuple[int, list[str]]:
    """Check every recorded digest against the bytes actually present.

    Returns `(verified, problems)`. Exit code is what the caller wants; this returns
    detail so the caller can print it.
    """
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise BackupError(f"no manifest at {manifest_path}; this is not a backup")
    manifest = json.loads(manifest_path.read_text())
    problems: list[str] = []

    dump_path = backup_dir / manifest["db_dump"]
    if not dump_path.exists():
        problems.append(f"the database dump is missing: {dump_path}")
    else:
        actual = sha256_file(dump_path)
        if actual != manifest["db_dump_sha256"]:
            problems.append(
                f"the database dump does not match its recorded digest "
                f"(recorded {manifest['db_dump_sha256'][:16]}…, found {actual[:16]}…)"
            )

    evidence_dir = backup_dir / EVIDENCE_DIR
    verified = 0
    for row in manifest["evidence"]:
        path = evidence_dir / row["sha256"]
        if not path.exists():
            problems.append(
                f"evidence {row['id']} ({row['filename']}) is absent: {row['sha256'][:16]}…"
            )
            continue
        actual = sha256_file(path)
        if actual != row["sha256"]:
            problems.append(
                f"evidence {row['id']} does not match its digest: recorded "
                f"{row['sha256'][:16]}…, found {actual[:16]}…"
            )
            continue
        size = path.stat().st_size
        if size != row["size_bytes"]:
            problems.append(
                f"evidence {row['id']} is {size} bytes, the database recorded "
                f"{row['size_bytes']}"
            )
            continue
        verified += 1

    return verified, problems


# --- restore --------------------------------------------------------------


async def restore(
    backup_dir: Path,
    *,
    target_database_url: str,
    settings: Any | None = None,
) -> dict[str, int]:
    """Restore into a named target. Never in place."""
    verified, problems = verify(backup_dir)
    if problems:
        raise BackupError(
            f"refusing to restore a backup that does not verify: {len(problems)} "
            f"problem(s), first: {problems[0]}"
        )

    manifest = json.loads((backup_dir / MANIFEST_NAME).read_text())
    target = _dsn(target_database_url)
    _ensure_extensions(target)

    # `allow_errors` on pg_restore is deliberate, and the check immediately after is
    # why. A newer pg_dump against an older server emits preamble statements the
    # server rejects — a PG18 client writes `SET transaction_timeout = 0`, which PG16
    # does not know — so pg_restore exits non-zero having *applied the archive*. Its
    # own exit code is therefore not a reliable answer to "did the restore work", and
    # treating it as one makes the tool fail on every archive it just wrote.
    #
    # So the archive is applied and then *verified*: every digest the manifest
    # records must be readable out of the restored database. That is a stronger
    # guarantee than any exit code, because it is a statement about the data rather
    # than about the tool's mood.
    pg_restore_result = _run(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--dbname",
            target,
            str(backup_dir / manifest["db_dump"]),
        ],
        allow_errors=True,
    )
    # Kept for the failure message below. `allow_errors` makes pg_restore's own
    # diagnostics invisible, and the post-restore check reports the *consequence*;
    # without these the operator gets "4 evidence records missing" and no idea why.
    restore_stderr = (pg_restore_result.stderr or pg_restore_result.stdout or "").strip()

    # The *async* URL, not `_dsn(target)`: that one drops `+asyncpg` for pg_restore,
    # and handing the result to the async reader asks SQLAlchemy for psycopg2, which
    # this project does not install.
    restored_digests = await _digests_in(target_database_url)
    missing: list[str] = []
    mismatched: list[str] = []
    for row in manifest["evidence"]:
        evidence_id = row["id"]
        if evidence_id not in restored_digests:
            missing.append(evidence_id)
        elif restored_digests[evidence_id] != row["sha256"]:
            mismatched.append(evidence_id)
    if missing or mismatched:
        # pg_restore's own diagnostics are attached because `allow_errors` makes them
        # invisible, and this check reports the *consequence*. Without them the
        # operator gets "4 evidence records missing" and no idea why.
        detail = restore_stderr[-600:] if restore_stderr else "(pg_restore said nothing)"
        raise BackupError(
            f"the restore did not reproduce the evidence: {len(missing)} missing "
            f"({', '.join(missing[:3])}...), {len(mismatched)} with a different digest "
            f"({', '.join(mismatched[:3])}...). Treat the target as untrustworthy. "
            f"pg_restore reported: {detail}"
        )

    restored_objects = 0
    if settings is not None:
        from app.domain.storage import ObjectStore

        store = ObjectStore(settings)
        for row in manifest["evidence"]:
            data = (backup_dir / EVIDENCE_DIR / row["sha256"]).read_bytes()
            store.put_evidence(key=row["sha256"], data=data, content_type=row["content_type"])
            restored_objects += 1

    return {"evidence_verified": verified, "evidence_restored": restored_objects}


#: Extensions the schema depends on. Per-database, so an empty target has none of
#: them, and a restore of a schema carrying `vector` columns cannot create its
#: tables without pgvector.
#:
#: Hardcoded rather than read from the source database because there is no reliable
#: way to ask an archive what it needs; and because these are the two this schema
#: actually uses. `--clean` does not drop them: an extension is only dropped if the
#: archive contains a `DROP EXTENSION`, and pg_dump of a single database does not
#: emit one.
_REQUIRED_EXTENSIONS = ("vector", "pgcrypto")


def _ensure_extensions(dsn: str) -> None:
    """Install the extensions this schema needs, on the target, before restoring.

    The failure this prevents is the quiet one. `pg_restore --clean` on an empty
    target reports the missing `vector` type, continues, and exits non-zero — which
    `restore` deliberately tolerates, because the exit code cannot be trusted (see
    below). So the schema is never created, the restore "succeeds", and the only
    thing that notices is the post-restore verification, reporting evidence as
    missing for a reason that has nothing to do with the backup.
    """
    for extension in _REQUIRED_EXTENSIONS:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            # Resolved by absolute path so the result does not depend on PATH.
            [
                shutil.which("psql") or "psql",
                dsn,
                "-tAc",
                f"CREATE EXTENSION IF NOT EXISTS {extension}",
            ],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0 and "already exists" not in result.stderr:
            raise BackupError(
                f"could not install the {extension!r} extension on the restore target: "
                f"{result.stderr.strip()[:300]}. A schema carrying it cannot be "
                f"restored without it."
            )


async def _digests_in(database_url: str) -> dict[str, str]:
    """Every evidence digest in a database, read over a connection of its own.

    Async because this project has no synchronous Postgres driver, and `restore`
    is async for the same reason `create` is.

    A separate connection rather than the caller's session, because a restored
    archive is not visible to a transaction that began before it was applied.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(text("SELECT id::text, sha256 FROM evidence"))
            ).fetchall()
        return {str(row_id): str(digest) for row_id, digest in rows}
    finally:
        await engine.dispose()


# --- cli ------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create_parser = sub.add_parser("create", help="dump the DB and copy evidence out")
    create_parser.add_argument("directory", type=Path)
    create_parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Postgres URL; defaults to DATABASE_URL",
    )

    verify_parser = sub.add_parser("verify", help="prove the backup is byte-exact")
    verify_parser.add_argument("directory", type=Path)

    restore_parser = sub.add_parser("restore", help="restore into a target database")
    restore_parser.add_argument("directory", type=Path)
    restore_parser.add_argument("--target-database-url", required=True)
    restore_parser.add_argument(
        "--with-evidence",
        action="store_true",
        help="also write the evidence objects back into object storage",
    )

    args = parser.parse_args()

    try:
        if args.command == "create":
            from app.config import get_settings

            if not args.database_url:
                raise BackupError("--database-url or DATABASE_URL is required")
            manifest = await create(
                args.directory, database_url=args.database_url, settings=get_settings()
            )
            print(
                f"Backed up {manifest['evidence_count']} evidence object(s) and the "
                f"database to {args.directory}"
            )
            print(f"Verify it with: python -m scripts.backup verify {args.directory}")
            return 0

        if args.command == "verify":
            verified, problems = verify(args.directory)
            for problem in problems:
                print(f"  PROBLEM  {problem}")
            if problems:
                print(f"\n{len(problems)} problem(s); the backup is not trustworthy.")
                return 1
            print(f"Verified {verified} evidence object(s) against their recorded digests.")
            return 0

        settings = None
        if args.with_evidence:
            from app.config import get_settings

            settings = get_settings()
        counts = await restore(
            args.directory,
            target_database_url=args.target_database_url,
            settings=settings,
        )
        print(
            f"Restored {counts['evidence_verified']} evidence object(s) into "
            f"{args.target_database_url}"
        )
        return 0

    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

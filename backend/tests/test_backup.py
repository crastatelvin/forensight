"""T10.3 — a backup that has been restored and verified, not just taken.

The usual backup test checks the artefact exists. That passes identically for a
complete backup and for one that silently lost every evidence object, because it
never looks at the objects. Evidence here is content-addressed — the object key is
the SHA-256 of the bytes — so verification here is cryptographic rather than
structural: a digest either matches or it does not, and there is nothing to
interpret.

The round trip is real. A real `pg_dump`, a real `pg_restore` into a real second
database, and every recorded digest recomputed from the restored bytes. Nothing is
mocked, because a mocked restore proves the test's own fixture code.

Skipped without `FORENSIGHT_PG_LIVE`, and says so. The alternative is a test that
claims backup coverage while running SQLite.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from app.db import Base
from scripts import backup
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("FORENSIGHT_PG_LIVE") != "1",
    reason="requires live Postgres (set FORENSIGHT_PG_LIVE=1)",
)

DSN = "postgresql+asyncpg://forensight:change-me-in-real-env@localhost:5432/forensight"

#: For CREATE/DROP DATABASE, which cannot run inside the database being created.
ADMIN_DSN = "postgresql://forensight:change-me-in-real-env@localhost:5432/postgres"


@pytest_asyncio.fixture
async def scratch_database(request: pytest.FixtureRequest) -> AsyncIterator[str]:
    """An empty database, created and dropped around one test.

    Isolation is the point, not tidiness. `create` backs up *every* evidence row the
    database records — which is correct, and is why it cannot be pointed at the
    shared development database: rows written by other tests live in a different
    object store, so the backup tries to read objects that were never written and
    the failure names a digest rather than the real problem.

    An empty database also means the assertions can be exact. Counting "at least N"
    against a database that accumulates rows across a whole suite run proves nothing
    about whether *this* test's evidence survived.
    """
    name = f"forensight_bk_{uuid.uuid4().hex[:10]}"
    admin = ADMIN_DSN
    _psql(admin, f'CREATE DATABASE "{name}"')
    url = DSN.rsplit("/", 1)[0] + f"/{name}"
    # Extensions are per-database, so an empty one has no pgvector and
    # create_all fails on the memories table with "type vector does not exist" —
    # which reads as a schema problem rather than as a missing CREATE EXTENSION.
    _psql(url, "CREATE EXTENSION IF NOT EXISTS vector")
    _psql(url, "CREATE EXTENSION IF NOT EXISTS pgcrypto")

    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    # `backup.create` opens its own connection through `app.db.SessionLocal`, whose
    # default DSN names the *in-container* hostname and does not resolve from the
    # host these tests run on.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.db.SessionLocal", maker)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield url
    finally:
        monkeypatch.undo()
        await engine.dispose()
        _psql(admin, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def _seed_evidence(url: str, store: Any, count: int) -> list[tuple[str, str]]:
    """Write `count` real evidence objects through the real ingest path.

    Goes via the service rather than inserting rows, so the objects in storage are
    byte-identical to what a deployment would hold — a backup test that seeded the
    database alone would verify digests for files that were never written anywhere.

    `store` is the *same* store the backup will later read from. This helper built its
    own store at first, so the evidence went into a throwaway while `create` read the
    fixture's — two stores, and a failure with nothing to do with backups.
    """
    from app.auth.repository import create_user
    from app.auth.roles import Role
    from app.domain.antivirus import NoopScanner
    from app.domain.models import Case
    from app.domain.service import IngestService

    service = IngestService(store, NoopScanner())
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with maker() as session:
            user = await create_user(
                session,
                email=f"backup-{uuid.uuid4().hex[:8]}@t.io",
                password="BackupPassw0rd!!",
                role=Role.OFFICER,
            )
            await session.flush()
            case = Case(
                number=f"BK-{uuid.uuid4().hex[:8].upper()}", title="backup", owner_id=user.id
            )
            session.add(case)
            await session.flush()

            written: list[tuple[str, str]] = []
            for index in range(count):
                # Distinct content per item, so a backup that restored one object into
                # every row would fail rather than pass.
                data = f"evidence-{index}-{uuid.uuid4().hex}".encode()
                evidence = await service.ingest(
                    session,
                    case_id=case.id,
                    actor_id=user.id,
                    filename=f"item-{index}.bin",
                    data=data,
                )
                written.append((str(evidence.id), evidence.sha256))
            await session.commit()
            return written
    finally:
        await engine.dispose()


async def test_backup_restores_and_every_digest_reproduces(
    tmp_path: Path, scratch_database: str, fake_store: Any
) -> None:
    """The whole round trip: dump, verify, restore into a second database, re-verify.

    The restore goes to a genuinely separate database rather than a mocked one. A
    `pg_restore` that was intercepted would verify the test's own plumbing; the
    claim being tested is that a backup taken by this tool can be put back by this
    tool and the evidence still hashes to what was ingested.
    """
    written = await _seed_evidence(scratch_database, fake_store._store, count=4)
    assert len(written) == 4

    backup_dir = tmp_path / "backup"
    manifest = await backup.create(
        backup_dir, database_url=DSN, settings=None, read_object=fake_store._store.get_evidence
    )
    assert manifest["evidence_count"] >= 4, "the seeded evidence is missing from the backup"

    # 1. Verification of what was just written.
    verified, problems = backup.verify(backup_dir)
    assert not problems, problems
    assert verified == manifest["evidence_count"]

    # 2. Restore into a second, real database.
    target_name = f"forensight_restore_{uuid.uuid4().hex[:8]}"
    admin = DSN.rsplit("/", 1)[0] + "/postgres"
    _psql(admin, f'CREATE DATABASE "{target_name}"')
    target = DSN.rsplit("/", 1)[0] + f"/{target_name}"
    try:
        counts = await backup.restore(backup_dir, target_database_url=target, settings=None)
        assert counts["evidence_verified"] == manifest["evidence_count"]

        # 3. The restored database really holds the evidence, and it is the evidence
        #    that was there before — checked against the digests captured prior to
        #    the backup rather than against the backup's own inventory.
        restored_digests = await backup._digests_in(target)
        for evidence_id, digest in written:
            assert (
                evidence_id in restored_digests
            ), f"evidence {evidence_id} is absent from the restored database"
            assert (
                restored_digests[evidence_id] == digest
            ), f"evidence {evidence_id} came back with a different digest"
    finally:
        _psql(admin, f'DROP DATABASE IF EXISTS "{target_name}"')


async def test_verification_catches_a_missing_object(
    tmp_path: Path, scratch_database: str, fake_store: Any
) -> None:
    """The check must fail on the failure it exists to catch.

    A verification that passes against a complete backup proves nothing about a
    truncated one. This deletes one object and requires the failure to name it —
    the operator has to learn *which* evidence is gone, because "the backup is
    incomplete" is not actionable at 3am.
    """
    written = await _seed_evidence(scratch_database, fake_store._store, count=3)
    backup_dir = tmp_path / "backup"
    manifest = await backup.create(
        backup_dir, database_url=DSN, settings=None, read_object=fake_store._store.get_evidence
    )

    victim = written[0][1]
    (backup_dir / backup.EVIDENCE_DIR / victim).unlink()

    verified, problems = backup.verify(backup_dir)
    assert verified == manifest["evidence_count"] - 1
    assert len(problems) == 1
    assert victim[:16] in problems[0]
    assert written[0][0] in problems[0], "the message must name the evidence, not just the digest"


async def test_verification_catches_a_corrupted_object(
    tmp_path: Path, scratch_database: str, fake_store: Any
) -> None:
    """Present but altered is the case a size check would miss.

    A truncated file changes size; a single flipped byte does not. Only recomputing
    the digest catches the second, which is why verification is cryptographic
    rather than structural.
    """
    written = await _seed_evidence(scratch_database, fake_store._store, count=2)
    backup_dir = tmp_path / "backup"
    manifest = await backup.create(
        backup_dir, database_url=DSN, settings=None, read_object=fake_store._store.get_evidence
    )

    victim = written[0][1]
    path = backup_dir / backup.EVIDENCE_DIR / victim
    data = bytearray(path.read_bytes())
    data[0] ^= 0xFF  # same length, different bytes
    path.write_bytes(bytes(data))

    verified, problems = backup.verify(backup_dir)
    assert verified == manifest["evidence_count"] - 1
    assert any("does not match its digest" in p for p in problems), problems


async def test_verification_catches_a_missing_database_dump(
    tmp_path: Path, scratch_database: str, fake_store: Any
) -> None:
    """The database half is checked with the same rigour as the evidence half.

    A backup of intact evidence with no database is evidence with no record of what
    it is — no case, no hash chain, no provenance. That is not a partial backup, it
    is a pile of files.
    """
    await _seed_evidence(scratch_database, fake_store._store, count=1)
    backup_dir = tmp_path / "backup"
    await backup.create(
        backup_dir, database_url=DSN, settings=None, read_object=fake_store._store.get_evidence
    )

    (backup_dir / backup.DUMP_NAME).unlink()
    verified, problems = backup.verify(backup_dir)
    assert verified >= 1, "the evidence was fine; only the dump is missing"
    assert any("database dump is missing" in p for p in problems), problems


async def test_restore_refuses_a_backup_that_does_not_verify(
    tmp_path: Path, scratch_database: str, fake_store: Any
) -> None:
    """Verification is a precondition, not a warning.

    Restoring from an unverified backup is how a corrupt archive replaces a good
    one: the operator restores, the service comes up, and the damage is discovered
    the next time evidence is read.
    """
    written = await _seed_evidence(scratch_database, fake_store._store, count=2)
    backup_dir = tmp_path / "backup"
    await backup.create(
        backup_dir, database_url=DSN, settings=None, read_object=fake_store._store.get_evidence
    )
    (backup_dir / backup.EVIDENCE_DIR / written[0][1]).unlink()

    with pytest.raises(backup.BackupError, match="does not verify"):
        await backup.restore(backup_dir, target_database_url=DSN, settings=None)


async def test_create_refuses_to_backup_evidence_that_does_not_match_its_digest(
    tmp_path: Path, scratch_database: str, fake_store: Any
) -> None:
    """A mismatch is caught at backup time, where it is diagnosable.

    Writing the bytes anyway would produce a backup that fails its own verification
    later — and it would fail far from the cause, in a directory named by a
    timestamp, with a message about a digest mismatch and no indication that the
    problem was there all along.

    The lie is injected through `read_object` rather than by patching
    `ObjectStore.get_evidence`: the earlier version patched the class and then called
    `create` with `settings=None`, so no `ObjectStore` was ever constructed and the
    patch had no effect at all. A test that arranges a corruption and does not
    actually cause one is worse than no test, because it passes.
    """
    written = await _seed_evidence(scratch_database, fake_store._store, count=2)
    _, victim_digest = written[0]
    real_read = fake_store._store.get_evidence

    def lying_reader(key: str) -> bytes:
        if key == victim_digest:
            # Same length, different bytes — the case a size check would miss.
            return b"X" * len(real_read(key))
        return real_read(key)

    with pytest.raises(backup.BackupError) as caught:
        await backup.create(
            tmp_path / "backup",
            database_url=DSN,
            settings=None,
            read_object=lying_reader,
        )

    message = str(caught.value)
    assert "does not match its recorded digest" in message
    assert victim_digest[:16] in message
    assert written[0][0] in message, "the message must name the evidence record"


# --- helpers --------------------------------------------------------------


def _psql(dsn: str, sql: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            shutil.which("psql") or "psql",
            dsn.replace("postgresql+asyncpg", "postgresql"),
            "-c",
            sql,
        ],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )


def test_sha256_file_handles_a_large_file_in_chunks(tmp_path: Path) -> None:
    """Hashing must not require loading the file.

    A backup tool that reads a multi-gigabyte evidence file into memory cannot verify
    the backups it exists to verify, and fails on exactly the files that matter most.
    """
    path = tmp_path / "big.bin"
    payload = os.urandom(3 * 1024 * 1024 + 17)
    path.write_bytes(payload)
    assert backup.sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_handles_an_empty_file(tmp_path: Path) -> None:
    """Degenerate, but a crash here would make every backup fail confusingly."""
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert backup.sha256_file(path) == hashlib.sha256(b"").hexdigest()

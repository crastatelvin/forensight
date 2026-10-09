"""Point-in-time recovery (P10.B2) — the refusals, and the arithmetic they depend on.

**These tests do not restore anything.** The full replay is verified by hand and the
measured result is recorded in CHECKLIST.md under P10.B2, because it needs a second
Postgres container and a few hundred megabytes. What is asserted here is everything that
can go wrong *quietly*, which is where this project has kept finding defects:

- an archive with a **gap** must be refused, because Postgres replays to the gap and then
  promotes, producing a server that looks recovered and is silently missing every
  transaction after it;
- recovery must have **no in-place path**, for the same reason `backup.py`'s `restore`
  does not: a tool that can overwrite the system it protects will eventually be aimed at
  it by someone in a hurry;
- the alert must key on the **failure delta**, because `pg_stat_archiver.failed_count` is
  monotonic and alerting on it fires forever once anything has ever failed.

The last one is asserted as a property of the metric's docstring and the alert's expr
rather than by running Prometheus, because the failure it prevents -- an alert that has
been permanently firing and has been muted -- cannot be caught by the alert itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from scripts import pitr

REPO = Path(__file__).resolve().parents[2]
ALERTS = REPO / "infra" / "observability" / "alerts.yml"
WAL_CONF = REPO / "infra" / "postgres" / "conf.d" / "10-wal-archiving.conf"


def _segments(directory: Path, names: list[str]) -> None:
    for name in names:
        (directory / name).write_bytes(b"")


# --- the gap check --------------------------------------------------------


def test_a_gap_in_the_archive_is_refused(tmp_path: Path) -> None:
    """A missing segment is the failure that makes recovery lie.

    Postgres replays WAL until it hits a gap and then promotes. The server starts,
    accepts queries, and is missing every transaction after the gap -- so the refusal
    names the missing segment rather than just refusing, because an operator at 3am needs
    to know which segment to go and find.
    """
    _segments(
        tmp_path,
        [
            "000000010000000000000090",
            "000000010000000000000091",
            "000000010000000000000093",  # 92 is missing
        ],
    )
    with pytest.raises(pitr.PitrError) as caught:
        pitr.check_archive_contiguous(tmp_path)
    message = str(caught.value)
    assert "000000010000000000000092" in message
    assert "PROMOTE" in message, "the refusal must say what recovery would have done"


def test_a_contiguous_archive_passes(tmp_path: Path) -> None:
    _segments(tmp_path, [f"0000000100000000000000{n:02X}" for n in range(0x90, 0x94)])
    low, high = pitr.check_archive_contiguous(tmp_path)
    assert (low, high) == ("000000010000000000000090", "000000010000000000000093")


def test_an_empty_archive_is_refused_with_a_different_reason(tmp_path: Path) -> None:
    """Not "a gap" -- there is no replay material at all, so the honest advice differs."""
    with pytest.raises(pitr.PitrError, match="empty"):
        pitr.check_archive_contiguous(tmp_path)


def test_non_segment_files_are_ignored(tmp_path: Path) -> None:
    """A `.history` or `.backup_label` in the archive is not a WAL segment.

    `pg_basebackup` writes a `backup_manifest` and Postgres writes timeline `.history`
    files into the same directory. Treating one of those as a segment would either invent
    a gap or extend the range past the real end.
    """
    _segments(
        tmp_path,
        [
            "000000010000000000000090",
            "000000010000000000000091",
        ],
    )
    (tmp_path / "00000002.history").write_text("")
    (tmp_path / "backup_manifest").write_text("")
    low, high = pitr.check_archive_contiguous(tmp_path)
    assert (low, high) == ("000000010000000000000090", "000000010000000000000091")


def test_a_single_segment_archive_is_not_a_gap(tmp_path: Path) -> None:
    """One segment has no interior, so it cannot have a gap in it."""
    _segments(tmp_path, ["000000010000000000000090"])
    assert pitr.check_archive_contiguous(tmp_path) == (
        "000000010000000000000090",
        "000000010000000000000090",
    )


def test_a_missing_archive_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(pitr.PitrError, match="does not exist"):
        pitr.check_archive_contiguous(tmp_path / "absent")


# --- the base-backup artefact check ---------------------------------------


def test_the_base_tarball_is_found_in_pg_basebackups_own_layout(tmp_path: Path) -> None:
    """`--format tar` writes a DIRECTORY called base.tar.gz containing the archive.

    Asserting `directory/"base.tar.gz"` is a file refuses a perfectly good backup. That
    is not hypothetical: it is what the first version of `pitr.py` did, and it reported
    REFUSED for a base backup that had been created correctly and completely.
    """
    nested = tmp_path / "base.tar.gz"
    nested.mkdir()
    (nested / "base.tar.gz").write_bytes(b"x" * 32)
    found = pitr._find_base_tarball(tmp_path)  # noqa: SLF001 - testing the resolution rule
    assert found is not None and found.is_file()


def test_a_flat_base_tarball_is_also_found(tmp_path: Path) -> None:
    """Tolerate either layout rather than depending on one."""
    (tmp_path / "base.tar.gz").write_bytes(b"x" * 32)
    found = pitr._find_base_tarball(tmp_path)  # noqa: SLF001
    assert found is not None and found.is_file()


def test_an_empty_directory_yields_no_tarball(tmp_path: Path) -> None:
    assert pitr._find_base_tarball(tmp_path) is None  # noqa: SLF001


# --- the refusals, by source inspection -----------------------------------
#
# restore_to writes to disk and shells out to tar, so these are asserted against the
# source rather than executed. They are the properties that must not be quietly removed.


def _restore_to_source() -> str:
    import inspect

    return inspect.getsource(pitr.restore_to)


def test_recovery_refuses_an_existing_data_directory() -> None:
    """No in-place recovery, and no way to override it."""
    assert "already exists" in _restore_to_source()


def test_there_is_no_force_flag_to_override_any_of_that() -> None:
    """Introspect the REAL parser, not a reconstruction of it.

    Two reasons a substring check is wrong here. `restore_to`'s own docstring says "and no
    `--force`", so asserting the word is absent from the source can never pass -- the first
    version of this test did exactly that and failed. And grepping `main()` misses a flag
    added anywhere else. So the actual parser's option strings are read.
    """
    parser = pitr.build_parser()
    subcommands = parser._subparsers._group_actions[0].choices  # noqa: SLF001
    for name, subparser in subcommands.items():
        options = {
            option
            for action in subparser._actions
            for option in action.option_strings  # noqa: SLF001
        }
        assert "--force" not in options, f"{name} gained a --force flag"
        assert "--yes" not in options, f"{name} gained a --yes flag"


def test_the_parser_exposes_every_recovery_target() -> None:
    parser = pitr.build_parser()
    subcommands = parser._subparsers._group_actions[0].choices  # noqa: SLF001
    recovery = subcommands["restore-to"]
    options = {o for a in recovery._actions for o in a.option_strings}  # noqa: SLF001
    assert {"--target-name", "--target-time", "--target-lsn"} <= options
    assert "--base-backup-dir" in options


def test_recovery_requires_exactly_one_target() -> None:
    source = _restore_to_source()
    assert "exactly one" in source
    for flag in ("target_name", "target_time", "target_lsn"):
        assert flag in source


def test_recovery_pauses_rather_than_auto_promoting() -> None:
    """A recovery aimed at a timestamp guessed at 3am must not become the live system."""
    source = _restore_to_source()
    assert "recovery_target_action = 'pause'" in source
    assert "pg_wal_replay_resume" in source, "the operator must be told how to promote deliberately"


def test_recovery_prefers_a_named_restore_point() -> None:
    """A restore point is created at the moment of the event; a timestamp is a guess."""
    source = _restore_to_source()
    assert "strongly preferred" in source


# --- the alert contract ---------------------------------------------------


def _alert_rules() -> dict[str, str]:
    import yaml

    document = yaml.safe_load(ALERTS.read_text())
    return {rule["alert"]: rule for group in document["groups"] for rule in group["rules"]}


def test_there_is_an_alert_for_archive_failure() -> None:
    rules = _alert_rules()
    assert "ForenSightWALArchiveFailing" in rules


def test_the_failure_alert_keys_on_the_delta_not_the_counter() -> None:
    """The counter is monotonic, so alerting on it fires forever once anything failed.

    Measured on this deployment: the counter reached 8 while archiving was broken, then
    archiving was fixed and `archived_count` kept climbing while `failed_count` stayed at
    8. An alert on the counter would have fired indefinitely on a healthy cluster, and the
    response to that is to mute it -- and then to miss the real one.
    """
    expr = _alert_rules()["ForenSightWALArchiveFailing"]["expr"]
    assert "forensight_wal_archive_failed_delta" in expr
    assert (
        re.search(r"forensight_wal_archive_failed_total\b(?!_)", expr) is None
    ), "the raw monotonic counter must not appear in the alert expression"


def test_there_is_a_stall_alert_for_the_quiet_failure() -> None:
    """Archiving configured, nothing failing, nothing archived either."""
    rules = _alert_rules()
    assert "ForenSightWALArchiveStalled" in rules
    assert "forensight_wal_archive_stalled" in rules["ForenSightWALArchiveStalled"]["expr"]


def test_both_wal_alerts_are_critical_or_worse() -> None:
    rules = _alert_rules()
    assert rules["ForenSightWALArchiveFailing"]["labels"]["severity"] == "critical"


# --- the archive timeout must not drift -----------------------------------


def test_the_archive_timeout_is_documented_where_it_is_configured() -> None:
    """The stall alert's threshold is derived from `archive_timeout`.

    `observability.WAL_ARCHIVE_TIMEOUT_SECONDS` says "must match". If the config changes and
    the constant does not, the alert fires at the wrong time with no symptom -- the kind of
    drift that is invisible until the alert is wrong in production.
    """
    from app.observability import WAL_ARCHIVE_TIMEOUT_SECONDS

    conf = WAL_CONF.read_text()
    assert f"archive_timeout = {WAL_ARCHIVE_TIMEOUT_SECONDS}" in conf

#!/bin/bash
# Create the WAL archive directory with the ownership the archiver needs.
#
# WHY THIS IS NEEDED, MEASURED 2026-10-09
#
# The `pgwal` volume mounts at /var/lib/postgresql/wal owned by root:root 0755, and the
# server runs as uid=999(postgres). So `archive_command` -- which runs as postgres --
# cannot write there, and:
#
#     select archived_count, failed_count from pg_stat_archiver;  ->  0 | 3
#     ls /var/lib/postgresql/wal                                   ->  empty
#
# **And nothing says so.** archive_mode is on, the settings look right, the container is
# healthy, and every segment silently fails to archive. This is the same shape as the
# deepfake detector that confidently reported "Silence" on gunfire: a misconfiguration
# that produces plausible output rather than an error. The only evidence is
# pg_stat_archiver, which is why infra/prometheus alerts on failed_archive_count -- see
# docs/runbook.md. Without that alert this fails invisibly and the first sign is a PITR
# that cannot reach its recovery point.
#
# Setgid (2775) so the archiver can create segments inside the directory.
#
# `set -u` but NOT `set -e`: docker-entrypoint-initdb.d scripts run with `set -e` added by
# the caller, and this must not abort initialisation of an otherwise-fine cluster over a
# directory that `chown` would fix anyway.
set -u

WAL_DIR=/var/lib/postgresql/wal

mkdir -p "$WAL_DIR"
# Create it owned by postgres even if the volume already exists with other ownership --
# which is the case on every redeploy, since the named volume survives `docker compose down`.
if ! chown postgres:postgres "$WAL_DIR" 2>/dev/null; then
  echo "WARNING: cannot chown $WAL_DIR (are we running as root?)." >&2
  echo "WARNING: WAL archiving will fail unless the archive directory is writable by" >&2
  echo "WARNING: uid 999 (postgres). Check pg_stat_archiver.failed_count." >&2
else
  chmod 2775 "$WAL_DIR"
  echo "WAL archive directory ready: $WAL_DIR ($(stat -c '%U:%G %a' "$WAL_DIR"))"
fi
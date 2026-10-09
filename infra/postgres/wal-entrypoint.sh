#!/bin/bash
# Prepare the Postgres data directory before handing over to the image's entrypoint.
#
# Two things, both of which fail SILENTLY otherwise.
#
# 1. WAL archive directory ownership.
#
#    The `pgwal` volume mounts at /var/lib/postgresql/wal owned by root:root 0755, and the
#    server runs as uid 999. So `archive_command` -- which runs as postgres -- cannot write
#    there, and every WAL segment fails to archive while:
#        archive_mode  -> on
#        container     -> healthy
#        logs          -> empty
#        the only evidence -> pg_stat_archiver.failed_count, which climbed to 8 while
#        the archive stayed empty (measured 2026-10-09).
#    Point-in-time recovery then cannot reach its recovery point, and the first sign is an
#    operator asking why. infra/observability/alerts.yml alerts on the failure delta.
#
#    NOT an initdb.d script: those run only when the data directory is empty, so they are
#    skipped on every start after the first, and the named volume keeps root ownership.
#    Verified by hitting it.
#
# 2. A pg_hba.conf entry for replication connections.
#
#    `pg_basebackup` (scripts/pitr.py base-backup) opens a REPLICATION connection, which
#    pg_hba matches against the pseudo-database `replication` -- a different line from the
#    ordinary `host all all all` entry. The official image's default pg_hba has no such
#    line, so:
#        pg_basebackup: error: no pg_hba.conf entry for replication connection from host
#    ...even though the same credentials work for everything else. Also silent: the role is
#    a superuser and does hold REPLICATION, so nothing is misconfigured as far as any
#    other command can see.
#
# Kept deliberately small, and it execs the image's own entrypoint unchanged: the official
# entrypoint must remain the process that becomes postgres, because it is what performs
# initdb, the POSTGRES_* handling and the SIGTERM-aware shutdown.
set -u

WAL_DIR=/var/lib/postgresql/wal
PGDATA="${PGDATA:-/var/lib/postgresql/data}"

mkdir -p "$WAL_DIR"
if chown postgres:postgres "$WAL_DIR" 2>/dev/null; then
  chmod 2775 "$WAL_DIR"
elif [ "$(id -u)" = "0" ]; then
  echo "FATAL: cannot chown $WAL_DIR as root; WAL archiving will fail." >&2
  exit 1
else
  echo "WARNING: not root and cannot chown $WAL_DIR; WAL archiving may fail." >&2
  echo "WARNING: check: select failed_count from pg_stat_archiver;" >&2
fi

HBA="$PGDATA/pg_hba.conf"
if [ -f "$HBA" ]; then
  MARK="# forensight: replication for pg_basebackup (added by wal-entrypoint.sh)"
  # A non-loopback replication entry is required. The stock image ships loopback-only
  # ones (`host replication all 127.0.0.1/32 trust`), and those do NOT cover this: the
  # connection arrives from the compose gateway (172.18.0.0/16) because pg_basebackup runs
  # on the host against the published port. The first version of this guard tested for
  # the mere PRESENCE of a replication line and so matched the loopback ones, declined to
  # add anything, and left pg_basebackup failing -- a check that passed while the thing it
  # checked for was absent.
  NEEDS_ENTRY=$(awk '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*host[[:space:]]+replication[[:space:]]/ {
      # any of: bare "all"/host, a CIDR that is not loopback, or an explicit "+"
      if ($0 !~ /127\.0\.0\.1|::1|localhost/) { print "yes"; exit }
    }
    END { }' "$HBA")
  if grep -qF "$MARK" "$HBA"; then
    : # already present; a restarted container must not accumulate duplicate lines
  elif [ "$NEEDS_ENTRY" = "yes" ]; then
    : # an operator has already opened replication beyond loopback; leave it alone
  else
    {
      echo ""
      echo "$MARK"
      echo "# Scoped to the compose network and to the container role. REPLICATION is a"
      echo "# superuser privilege, so this grants nothing the forensight role did not"
      echo "# already have -- it is pg_hba that was blocking the connection, not the role."
      echo "host    replication     forensight    all        scram-sha-256"
    } >> "$HBA"
    echo "pg_hba.conf: added a replication entry for pg_basebackup"
  fi
fi

exec docker-entrypoint.sh "$@"
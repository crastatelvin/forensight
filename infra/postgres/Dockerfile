# Postgres 16 + pgvector + PostGIS on one instance.
#
# Built from pgvector's own image rather than postgis': the stock postgis image
# is Debian bullseye, whose PGDG apt repo now 404s, so installing the pgvector
# package into it fails. Going the other way — pgvector's bookworm base plus the
# PostGIS package — resolves cleanly. One instance for relational + vector +
# geospatial keeps the ops story simple (DESIGN_PLAN decision 3).
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*
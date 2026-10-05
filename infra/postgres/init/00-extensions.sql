-- Extensions enabled on first init.
--
-- postgis: geospatial analysis (Phase 9).
-- vector:  pgvector embeddings for agent memory (Phase 5).
--
-- Both come from the image built from ./Dockerfile. The guard matters: a failing
-- statement in docker-entrypoint-initdb.d aborts the whole sequence, so Postgres
-- never becomes healthy and the entire stack fails. Degrading with a warning
-- beats failing to boot.
--
-- SQLERRM is interpolated as a placeholder argument; a literal '%' in the
-- message would be read as a second parameter and abort the DO block itself.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS postgis;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'postgis unavailable: % -- geospatial analyzers disabled', SQLERRM;
END
$$;

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'pgvector unavailable: % -- agent memory disabled', SQLERRM;
END
$$;
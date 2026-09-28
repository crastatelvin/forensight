-- Enable required PostgreSQL extensions on first init.
-- postgis: geospatial (Phase 9). vector: pgvector embeddings (Phase 5).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

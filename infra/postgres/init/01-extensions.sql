-- Runs once, when the PostgreSQL data volume is first initialised.
-- Enables pgvector only; no application schemas are created here.
CREATE EXTENSION IF NOT EXISTS vector;

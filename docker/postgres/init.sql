-- init.sql
-- Runs once, when the database volume is first created (not on later starts).
-- pgvector powers retrieval over past incidents and runbooks (docs/design_architecture.md).
CREATE EXTENSION IF NOT EXISTS vector;

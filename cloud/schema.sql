-- swafra cloud schema — pool tenancy with Row Level Security (ADR-0002).
--
-- Pattern: AWS Database Blog "Multi-tenant data isolation with PostgreSQL
-- Row Level Security" + AWS Prescriptive Guidance (SaaS on managed Postgres):
--   * ENABLE + FORCE row level security (table owner obeys policies too)
--   * policy compares tenant columns to transaction-local GUCs
--   * current_setting(..., true) returns NULL when unset -> FAILS CLOSED
--   * app connects as a NON-OWNER role without BYPASSRLS; context is set per
--     transaction via set_config(..., true), safe with connection pooling
--
-- Apply as an admin/owner role:   python -m cloud.pgstore init
-- Then grant the app role once:   python -m cloud.pgstore grant --role swafra_app
-- (create the login role yourself: CREATE ROLE swafra_app LOGIN PASSWORD '...';)

CREATE SCHEMA IF NOT EXISTS swafra;
SET search_path TO swafra;

-- ---------------------------------------------------------------------------
-- Tenant data (RLS-protected). Columns mirror engine/storage.py so the
-- PostgresStore adapter is a faithful MemoryStore port. Embeddings are bytea
-- (dimension-agnostic, identical codec to the SQLite tier); retrieval scoring
-- happens in the engine, per workspace. pgvector ANN is a deliberate later
-- optimization once the engine grows an ANN-aware retrieval path.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chunks (
    tenant_id     text NOT NULL,
    workspace_id  text NOT NULL,
    id            text NOT NULL,
    source_id     text NOT NULL,
    source_title  text NOT NULL DEFAULT '',
    content       text NOT NULL,
    embedding     bytea,
    token_count   integer DEFAULT 0,
    chunk_index   integer DEFAULT 0,
    community_id  integer DEFAULT 0,
    entities      jsonb NOT NULL DEFAULT '[]',
    dates         jsonb NOT NULL DEFAULT '[]',
    preferences   jsonb NOT NULL DEFAULT '[]',
    type          text DEFAULT 'unknown',
    span          jsonb NOT NULL DEFAULT '[]',
    created_at    double precision,
    superseded_by text,
    PRIMARY KEY (tenant_id, workspace_id, id)
);
CREATE INDEX IF NOT EXISTS idx_chunks_source
    ON chunks (tenant_id, workspace_id, source_id);

CREATE TABLE IF NOT EXISTS edges (
    seq           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     text NOT NULL,
    workspace_id  text NOT NULL,
    source_id     text,
    from_id       text NOT NULL,
    to_id         text NOT NULL,
    type          text NOT NULL,
    weight        double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_ws
    ON edges (tenant_id, workspace_id, source_id);

CREATE TABLE IF NOT EXISTS sources (
    tenant_id     text NOT NULL,
    workspace_id  text NOT NULL,
    id            text NOT NULL,
    title         text NOT NULL,
    chunks        integer,
    PRIMARY KEY (tenant_id, workspace_id, id)
);

CREATE TABLE IF NOT EXISTS facts_data (
    tenant_id     text NOT NULL,
    workspace_id  text NOT NULL,
    id            text NOT NULL,
    chunk_id      text,
    source_id     text,
    data          jsonb NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, id)
);

-- RLS: enable, FORCE (owner too), fail-closed policies on both tenant axes.
ALTER TABLE chunks     ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks     FORCE  ROW LEVEL SECURITY;
ALTER TABLE edges      ENABLE ROW LEVEL SECURITY;
ALTER TABLE edges      FORCE  ROW LEVEL SECURITY;
ALTER TABLE sources    ENABLE ROW LEVEL SECURITY;
ALTER TABLE sources    FORCE  ROW LEVEL SECURITY;
ALTER TABLE facts_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE facts_data FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON chunks;
CREATE POLICY tenant_isolation ON chunks
    USING (tenant_id = current_setting('app.tenant_id', true)
       AND workspace_id = current_setting('app.workspace_id', true));

DROP POLICY IF EXISTS tenant_isolation ON edges;
CREATE POLICY tenant_isolation ON edges
    USING (tenant_id = current_setting('app.tenant_id', true)
       AND workspace_id = current_setting('app.workspace_id', true));

DROP POLICY IF EXISTS tenant_isolation ON sources;
CREATE POLICY tenant_isolation ON sources
    USING (tenant_id = current_setting('app.tenant_id', true)
       AND workspace_id = current_setting('app.workspace_id', true));

DROP POLICY IF EXISTS tenant_isolation ON facts_data;
CREATE POLICY tenant_isolation ON facts_data
    USING (tenant_id = current_setting('app.tenant_id', true)
       AND workspace_id = current_setting('app.workspace_id', true));

-- ---------------------------------------------------------------------------
-- Ingest job queue (ADR-0004). Deliberately NOT under RLS: workers claim work
-- across tenants (a queue is infrastructure, like SQS — not tenant documents).
-- Tenant scoping of reads/dedup is enforced in cloud/jobs.py; job ids are
-- unguessable uuid4. Claiming uses FOR UPDATE SKIP LOCKED; crashed workers
-- are recovered by lease (locked_until) expiry.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS jobs (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL,
    workspace_id  text NOT NULL,
    content_hash  text NOT NULL,
    title         text NOT NULL,
    text          text NOT NULL,
    status        text NOT NULL DEFAULT 'pending',  -- pending|processing|ready|failed
    attempts      integer NOT NULL DEFAULT 0,
    max_attempts  integer NOT NULL DEFAULT 3,
    run_at        double precision NOT NULL,
    locked_until  double precision,
    locked_by     text,
    result        text,
    error         text,
    created_at    double precision NOT NULL,
    updated_at    double precision NOT NULL
);
-- Partial index keeps the claim query fast regardless of finished-job volume.
CREATE INDEX IF NOT EXISTS idx_jobs_runnable
    ON jobs (run_at) WHERE status IN ('pending', 'processing');
CREATE INDEX IF NOT EXISTS idx_jobs_dedup
    ON jobs (tenant_id, workspace_id, content_hash);

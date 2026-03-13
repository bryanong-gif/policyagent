-- migrations/001_initial_schema.sql
-- Run once against your Postgres database to set up the schema.
-- Usage: psql $DATABASE_URL -f migrations/001_initial_schema.sql

-- ──────────────────────────────────────────────────────────
-- ENUMS
-- ──────────────────────────────────────────────────────────
CREATE TYPE jurisdiction_t AS ENUM ('sg','au','uk','eu','asean','global');
CREATE TYPE domain_t       AS ENUM ('online_safety','ai_safety','tech_governance','other');
CREATE TYPE urgency_t      AS ENUM ('monitoring','notable','urgent');
CREATE TYPE sentiment_t    AS ENUM ('regulatory_tightening','regulatory_loosening','neutral');
CREATE TYPE content_type_t AS ENUM (
  'legislation','consultation','enforcement','enforcement_action',
  'guidance','academic','news','speech','other'
);

-- ──────────────────────────────────────────────────────────
-- ITEMS
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS items (
    id              BIGSERIAL PRIMARY KEY,
    url_hash        CHAR(32)        UNIQUE NOT NULL,
    source_id       TEXT            NOT NULL,
    title           TEXT            NOT NULL,
    url             TEXT            NOT NULL,
    published       TIMESTAMPTZ,
    jurisdiction    jurisdiction_t  NOT NULL,
    domain          domain_t,
    content_type    content_type_t,
    urgency         urgency_t       NOT NULL DEFAULT 'monitoring',
    sentiment       sentiment_t     NOT NULL DEFAULT 'neutral',
    relevance_score SMALLINT        NOT NULL DEFAULT 5 CHECK (relevance_score BETWEEN 1 AND 10),
    summary         TEXT,
    key_points      JSONB           DEFAULT '[]',
    tags            JSONB           DEFAULT '[]',
    implications    TEXT,
    raw_domains     JSONB           DEFAULT '[]',
    notified        BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_items_jurisdiction  ON items (jurisdiction);
CREATE INDEX idx_items_domain        ON items (domain);
CREATE INDEX idx_items_urgency       ON items (urgency);
CREATE INDEX idx_items_created_at    ON items (created_at DESC);
CREATE INDEX idx_items_relevance     ON items (relevance_score DESC);
CREATE INDEX idx_items_notified      ON items (notified) WHERE notified = FALSE;

-- Full-text search
ALTER TABLE items ADD COLUMN IF NOT EXISTS search_vector TSVECTOR
    GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title,'') || ' ' || coalesce(summary,''))
    ) STORED;

CREATE INDEX idx_items_fts ON items USING GIN (search_vector);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$;

CREATE TRIGGER items_updated_at
    BEFORE UPDATE ON items
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ──────────────────────────────────────────────────────────
-- DIGESTS
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS digests (
    id           BIGSERIAL PRIMARY KEY,
    period_start TIMESTAMPTZ,
    period_end   TIMESTAMPTZ,
    item_count   INTEGER,
    synthesis    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────────
-- SOURCES LOG  (optional: track fetch health)
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS source_runs (
    id          BIGSERIAL PRIMARY KEY,
    source_id   TEXT        NOT NULL,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    items_found INTEGER     DEFAULT 0,
    items_kept  INTEGER     DEFAULT 0,
    error       TEXT
);

CREATE INDEX idx_source_runs_source_id ON source_runs (source_id, run_at DESC);

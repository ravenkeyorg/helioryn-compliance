-- Copyright (c) 2026 Ravenkey LLC. All rights reserved.
-- 001_initial: Layer 1 event-sourced schema
-- Event table: append-only log of every source ingestion
-- Snapshot table: materialized latest state per source_id

CREATE TABLE IF NOT EXISTS source_ingested (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL,
    source_url      TEXT NOT NULL,
    title           TEXT,
    author          TEXT,
    publish_date    TIMESTAMPTZ,
    retrieved_at    TIMESTAMPTZ NOT NULL,
    raw_text        TEXT NOT NULL,
    raw_html        TEXT,
    content_hash    TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}'::jsonb,
    retrieval_method TEXT NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_source_ingested_source_id ON source_ingested (source_id);
CREATE INDEX idx_source_ingested_content_hash ON source_ingested (content_hash);
CREATE INDEX idx_source_ingested_ingested_at ON source_ingested (ingested_at);

CREATE TABLE IF NOT EXISTS source_snapshot (
    source_id       UUID PRIMARY KEY,
    source_url      TEXT NOT NULL,
    title           TEXT,
    author          TEXT,
    publish_date    TIMESTAMPTZ,
    retrieved_at    TIMESTAMPTZ NOT NULL,
    raw_text        TEXT NOT NULL,
    raw_html        TEXT,
    content_hash    TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}'::jsonb,
    retrieval_method TEXT NOT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    last_updated_at TIMESTAMPTZ NOT NULL
);

-- Trigger function: upsert snapshot on event insert
CREATE OR REPLACE FUNCTION update_source_snapshot()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO source_snapshot (
        source_id, source_url, title, author, publish_date,
        retrieved_at, raw_text, raw_html, content_hash,
        metadata, retrieval_method, first_seen_at, last_updated_at
    ) VALUES (
        NEW.source_id, NEW.source_url, NEW.title, NEW.author, NEW.publish_date,
        NEW.retrieved_at, NEW.raw_text, NEW.raw_html, NEW.content_hash,
        NEW.metadata, NEW.retrieval_method, NEW.ingested_at, NEW.ingested_at
    )
    ON CONFLICT (source_id) DO UPDATE SET
        source_url = EXCLUDED.source_url,
        title = EXCLUDED.title,
        author = EXCLUDED.author,
        publish_date = EXCLUDED.publish_date,
        retrieved_at = EXCLUDED.retrieved_at,
        raw_text = EXCLUDED.raw_text,
        raw_html = EXCLUDED.raw_html,
        content_hash = EXCLUDED.content_hash,
        metadata = EXCLUDED.metadata,
        retrieval_method = EXCLUDED.retrieval_method,
        last_updated_at = EXCLUDED.last_updated_at;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_source_snapshot ON source_ingested;
CREATE TRIGGER trg_update_source_snapshot
    AFTER INSERT ON source_ingested
    FOR EACH ROW
    EXECUTE FUNCTION update_source_snapshot();

-- Upgrades: add columns to existing tables
ALTER TABLE source_ingested ADD COLUMN IF NOT EXISTS author TEXT;
ALTER TABLE source_ingested ADD COLUMN IF NOT EXISTS publish_date TIMESTAMPTZ;
ALTER TABLE source_ingested ADD COLUMN IF NOT EXISTS raw_html TEXT;

ALTER TABLE source_snapshot ADD COLUMN IF NOT EXISTS author TEXT;
ALTER TABLE source_snapshot ADD COLUMN IF NOT EXISTS publish_date TIMESTAMPTZ;
ALTER TABLE source_snapshot ADD COLUMN IF NOT EXISTS raw_html TEXT;

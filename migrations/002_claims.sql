-- Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
-- 002_claims: Layer 2 claim extraction
-- Each claim is an atomic assertion extracted from a source

CREATE TABLE IF NOT EXISTS claim (
    claim_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id        UUID NOT NULL REFERENCES source_snapshot(source_id) ON DELETE CASCADE,
    source_url       TEXT NOT NULL,
    extracted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    canonical_text   TEXT NOT NULL,
    original_text    TEXT NOT NULL,
    extraction_confidence REAL DEFAULT 1.0,
    entities         JSONB DEFAULT '[]'::jsonb,
    claim_type       TEXT DEFAULT 'fact',
    context_sentence TEXT
);

CREATE INDEX IF NOT EXISTS idx_claim_source_id ON claim (source_id);
CREATE INDEX IF NOT EXISTS idx_claim_type ON claim (claim_type);
CREATE INDEX IF NOT EXISTS idx_claim_canonical ON claim USING gin (to_tsvector('english', canonical_text));

CREATE TABLE IF NOT EXISTS entity (
    entity_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    entity_type  TEXT NOT NULL,  -- 'person', 'organization', 'location', 'event', 'concept'
    claim_ids    UUID[] DEFAULT '{}',
    external_ids JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_entity_name ON entity (name);
CREATE INDEX IF NOT EXISTS idx_entity_type ON entity (entity_type);

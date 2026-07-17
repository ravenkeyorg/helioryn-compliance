-- Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
-- 005_entity: Layer 3a entity extraction table
-- Stores entities extracted from claims, linked via claim_entity join table.

CREATE TABLE IF NOT EXISTS entity (
    entity_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    entity_type  TEXT NOT NULL DEFAULT 'concept',
    external_ids JSONB DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_name ON entity (name);

CREATE TABLE IF NOT EXISTS claim_entity (
    claim_id  UUID NOT NULL,
    entity_id UUID NOT NULL,
    PRIMARY KEY (claim_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_ce_claim_id ON claim_entity (claim_id);
CREATE INDEX IF NOT EXISTS idx_ce_entity_id ON claim_entity (entity_id);

ALTER TABLE entity ADD COLUMN IF NOT EXISTS external_ids JSONB DEFAULT '{}'::jsonb;

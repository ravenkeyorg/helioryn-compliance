-- Copyright (c) 2026 Ravenkey LLC. All rights reserved.
-- 004_observation: Layer 4 claim-level observation tracking
-- Records every time a claim is observed from a source.
-- Append-only: never mutated, never deleted.

CREATE TABLE IF NOT EXISTS claim_observation (
    observation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id       UUID NOT NULL,
    source_id      UUID NOT NULL,
    observed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    observer       TEXT NOT NULL DEFAULT 'helioryn-ingest',
    context        TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_claim_id ON claim_observation (claim_id);
CREATE INDEX IF NOT EXISTS idx_obs_source_id ON claim_observation (source_id);
CREATE INDEX IF NOT EXISTS idx_obs_observed_at ON claim_observation (observed_at);

ALTER TABLE claim_observation ADD COLUMN IF NOT EXISTS observer TEXT;

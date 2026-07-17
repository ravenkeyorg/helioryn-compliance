-- Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
-- 007_narrative: Layer 5 narrative clusters
-- Groups claims into evolving story topics

CREATE TABLE IF NOT EXISTS narrative (
    narrative_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    description   TEXT,
    top_terms     TEXT[] DEFAULT '{}',
    claim_count   INT DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active     BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS narrative_claim (
    narrative_id UUID NOT NULL REFERENCES narrative(narrative_id) ON DELETE CASCADE,
    claim_id     UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
    weight       REAL DEFAULT 0.0,
    PRIMARY KEY (narrative_id, claim_id)
);

CREATE INDEX IF NOT EXISTS idx_nc_narrative ON narrative_claim (narrative_id);
CREATE INDEX IF NOT EXISTS idx_nc_claim ON narrative_claim (claim_id);

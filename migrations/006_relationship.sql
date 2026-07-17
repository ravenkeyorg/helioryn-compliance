-- Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
-- 006_relationship: Layer 3b/3c claim relationship graph
-- Same-claim detection (embedding similarity) + contradiction detection
-- Requires pgvector extension

CREATE TABLE IF NOT EXISTS claim_embedding (
    embedding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id     UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
    embedding    vector(384) NOT NULL,
    model_name   TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ce_claim_id ON claim_embedding (claim_id);
CREATE INDEX IF NOT EXISTS idx_ce_embedding ON claim_embedding
    USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS claim_relationship (
    relationship_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_claim_id   UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
    target_claim_id   UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    confidence        REAL NOT NULL DEFAULT 1.0,
    detected_by       TEXT NOT NULL DEFAULT 'rule',
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    evidence          TEXT,
    CONSTRAINT chk_no_self CHECK (source_claim_id != target_claim_id)
);

CREATE INDEX IF NOT EXISTS idx_rel_source ON claim_relationship (source_claim_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON claim_relationship (target_claim_id);
CREATE INDEX IF NOT EXISTS idx_rel_type ON claim_relationship (relationship_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_pair
    ON claim_relationship (source_claim_id, target_claim_id, relationship_type);

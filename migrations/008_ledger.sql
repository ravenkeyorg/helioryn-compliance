-- Copyright (c) 2026 Ravenkey LLC. All rights reserved.
-- 008_ledger: Immutable hash chain ledger for cryptographic provenance
-- Every source fetch, claim extraction, confidence factor, narrative assignment,
-- and staging review is recorded in an append-only chain with SHA256 hashes.
-- Tampering with any historical record breaks the hash chain.

CREATE TABLE IF NOT EXISTS ledger (
    id             BIGSERIAL PRIMARY KEY,
    entry_type     TEXT NOT NULL,
    claim_id       UUID REFERENCES claim(claim_id) ON DELETE SET NULL,
    source_id      UUID,
    data_hash      TEXT NOT NULL,
    previous_hash  TEXT NOT NULL,
    metadata       JSONB DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ledger_claim_id ON ledger (claim_id);
CREATE INDEX IF NOT EXISTS idx_ledger_source_id ON ledger (source_id);
CREATE INDEX IF NOT EXISTS idx_ledger_entry_type ON ledger (entry_type);
CREATE INDEX IF NOT EXISTS idx_ledger_created_at ON ledger (created_at);

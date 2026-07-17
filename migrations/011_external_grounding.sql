-- Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
ALTER TABLE claim ADD COLUMN IF NOT EXISTS wikidata_qid TEXT DEFAULT '';
ALTER TABLE claim ADD COLUMN IF NOT EXISTS factcheck_count INT DEFAULT 0;
ALTER TABLE claim ADD COLUMN IF NOT EXISTS factcheck_agreement REAL DEFAULT 0.0;

CREATE TABLE IF NOT EXISTS external_factcheck (
    factcheck_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id        UUID NOT NULL REFERENCES claim(claim_id),
    source          TEXT NOT NULL,
    rating          TEXT NOT NULL,
    publisher       TEXT,
    review_url      TEXT,
    review_date     TIMESTAMPTZ,
    confidence      REAL DEFAULT 0.5,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ef_claim ON external_factcheck (claim_id);

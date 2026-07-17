-- Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
-- 009: Add topic column to claim table for topic-scoped filtering.
-- Topics are mutually exclusive — each claim belongs to exactly one topic.

ALTER TABLE claim ADD COLUMN topic TEXT;
CREATE INDEX idx_claim_topic ON claim(topic);

ALTER TABLE claim_version ADD COLUMN topic TEXT;

-- Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
-- 003_discovery: query engine and government entity tracking

CREATE TABLE IF NOT EXISTS search_query (
    query_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text         TEXT NOT NULL,
    language     TEXT DEFAULT 'all',
    source       TEXT,                   -- 'seed', 'entity', 'term', 'human'
    parent_query TEXT,                   -- what led to this query
    priority     INT DEFAULT 50,
    interval_m   INT DEFAULT 360,
    last_run     TIMESTAMPTZ,
    active       BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_search_query_text ON search_query (text);
CREATE INDEX IF NOT EXISTS idx_search_query_priority ON search_query (priority, last_run);

CREATE TABLE IF NOT EXISTS government_entity (
    entity_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    level         TEXT NOT NULL,       -- 'country', 'state', 'city', 'international', 'agency'
    country       TEXT,                -- ISO code
    region        TEXT,
    search_name   TEXT NOT NULL,
    aliases       TEXT[] DEFAULT '{}',
    active        BOOLEAN DEFAULT TRUE,
    discovered_by TEXT,                -- 'seed', 'ner', 'url', 'human'
    last_searched TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gov_entity_level ON government_entity (level);
CREATE INDEX IF NOT EXISTS idx_gov_entity_country ON government_entity (country);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gov_entity_name ON government_entity (name);

-- Copyright (c) 2026 Ravenkey LLC. All rights reserved.
ALTER TABLE search_query ADD COLUMN IF NOT EXISTS category TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_search_query_category ON search_query (category);

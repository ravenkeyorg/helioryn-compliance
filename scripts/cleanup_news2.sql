BEGIN;

-- Create temp table using CREATE TABLE AS (faster than SELECT INTO in some cases)
CREATE TEMP TABLE tmp_claims AS
SELECT c.claim_id
FROM claim c
JOIN source_snapshot ss ON ss.source_id = c.source_id
WHERE ss.retrieval_method = 'http_fetch';

-- Analyze for better query planning
ANALYZE tmp_claims;

-- Batch delete child tables using USING (faster than IN subquery)
DELETE FROM claim_grounding cg
USING tmp_claims tc
WHERE cg.claim_id = tc.claim_id;

DELETE FROM claim_relationship cr
USING tmp_claims tc
WHERE cr.source_claim_id = tc.claim_id;

DELETE FROM claim_relationship cr
USING tmp_claims tc
WHERE cr.target_claim_id = tc.claim_id;

DELETE FROM narrative_claim nc
USING tmp_claims tc
WHERE nc.claim_id = tc.claim_id;

DELETE FROM claim_version cv
USING tmp_claims tc
WHERE cv.claim_id = tc.claim_id;

UPDATE ledger l SET claim_id = NULL
FROM tmp_claims tc
WHERE l.claim_id = tc.claim_id;

DELETE FROM claim_embedding ce
USING tmp_claims tc
WHERE ce.claim_id = tc.claim_id;

DELETE FROM claim_mutation cm
USING tmp_claims tc
WHERE cm.source_claim_id = tc.claim_id;

DELETE FROM claim_mutation cm
USING tmp_claims tc
WHERE cm.target_claim_id = tc.claim_id;

-- Now delete claims
DELETE FROM claim c
USING tmp_claims tc
WHERE c.claim_id = tc.claim_id;

-- source_behavior
DELETE FROM source_behavior sb
USING source_snapshot ss
WHERE ss.retrieval_method = 'http_fetch'
AND sb.source_id = ss.source_id;

-- source_snapshot
DELETE FROM source_snapshot
WHERE retrieval_method = 'http_fetch';

DROP TABLE IF EXISTS tmp_claims;

COMMIT;

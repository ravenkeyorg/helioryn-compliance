-- Cleanup stale http_fetch news articles (run as single transaction)
BEGIN;

-- Create temp table with claim_ids to delete
SELECT c.claim_id
INTO TEMP TABLE to_delete_claims
FROM claim c
JOIN source_snapshot ss ON ss.source_id = c.source_id
WHERE ss.retrieval_method = 'http_fetch';

-- 1. claim_grounding
DELETE FROM claim_grounding WHERE claim_id IN (SELECT claim_id FROM to_delete_claims);

-- 2. claim_relationship
DELETE FROM claim_relationship WHERE source_claim_id IN (SELECT claim_id FROM to_delete_claims);
DELETE FROM claim_relationship WHERE target_claim_id IN (SELECT claim_id FROM to_delete_claims);

-- 3. narrative_claim
DELETE FROM narrative_claim WHERE claim_id IN (SELECT claim_id FROM to_delete_claims);

-- 4. claim_version
DELETE FROM claim_version WHERE claim_id IN (SELECT claim_id FROM to_delete_claims);

-- 5. ledger
UPDATE ledger SET claim_id = NULL WHERE claim_id IN (SELECT claim_id FROM to_delete_claims);

-- 6. claim_embedding
DELETE FROM claim_embedding WHERE claim_id IN (SELECT claim_id FROM to_delete_claims);

-- 7. claim_mutation
DELETE FROM claim_mutation WHERE source_claim_id IN (SELECT claim_id FROM to_delete_claims);
DELETE FROM claim_mutation WHERE target_claim_id IN (SELECT claim_id FROM to_delete_claims);

-- 8. claim
DELETE FROM claim WHERE claim_id IN (SELECT claim_id FROM to_delete_claims);

-- 9. source_behavior
DELETE FROM source_behavior WHERE source_id IN (
    SELECT source_id FROM source_snapshot WHERE retrieval_method = 'http_fetch'
);

-- 10. source_snapshot
DELETE FROM source_snapshot WHERE retrieval_method = 'http_fetch';

DROP TABLE IF EXISTS to_delete_claims;

COMMIT;

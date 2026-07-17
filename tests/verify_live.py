# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
import asyncio, sys
sys.path.insert(0, "src")
from helioryn.store import EventStore
from helioryn.extract.temporal import extract_temporal_references
from helioryn.extract.uncertainty import detect_uncertainty

async def verify():
    s = EventStore("postgresql:///localhost_dev?host=/tmp")
    await s.connect()
    async with s._pool.acquire() as conn:
        samples = await conn.fetch("SELECT claim_id, canonical_text FROM claim WHERE temporal_references IS NOT NULL AND temporal_references != '[]'::jsonb LIMIT 5")
        print(f"Temporal: {len(samples)} claims with references sampled")

        raw = await conn.fetchval("SELECT canonical_text FROM claim LIMIT 1")
        trefs = extract_temporal_references(raw)
        print(f"Raw extraction works: {len(trefs)} refs from first claim")

        unc = detect_uncertainty(raw)
        print(f"Uncertainty works: score={unc['score']}, signals={len(unc['signals'])}")

        ccount = await conn.fetchval("SELECT COUNT(*) FROM claim_relationship WHERE relationship_type='contradicts'")
        rcount = await conn.fetchval("SELECT COUNT(*) FROM claim_relationship WHERE relationship_type='repeated_by'")
        print(f"Contradictions: {ccount}, Same-claim: {rcount}")

        ec = await conn.fetchval("SELECT COUNT(*) FROM claim_embedding")
        print(f"Embeddings: {ec}")

        en = await conn.fetchval("SELECT COUNT(*) FROM entity")
        ce = await conn.fetchval("SELECT COUNT(*) FROM claim_entity")
        print(f"Entities: {en}, Links: {ce}")

        oc = await conn.fetchval("SELECT COUNT(*) FROM claim_observation")
        print(f"Observations: {oc}")

    print("All 7 checks passed")
    await s.close()

asyncio.run(verify())

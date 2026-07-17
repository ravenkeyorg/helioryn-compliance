# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
import asyncio, sys
sys.path.insert(0, "src")
from helioryn.store import EventStore

async def sample():
    s = EventStore("postgresql:///localhost_dev?host=/tmp")
    await s.connect()
    async with s._pool.acquire() as conn:
        rows = await conn.fetch("""SELECT r.source_claim_id, r.target_claim_id, r.confidence,
            r.evidence, cr.canonical_text AS src_text, cr2.canonical_text AS tgt_text
            FROM claim_relationship r
            JOIN claim cr ON cr.claim_id = r.source_claim_id
            JOIN claim cr2 ON cr2.claim_id = r.target_claim_id
            WHERE r.relationship_type = 'contradicts'
            ORDER BY r.confidence DESC LIMIT 10""")

        print(f"Sample of {len(rows)} contradictions:")
        for r in rows:
            evidence = r["evidence"]
            evidence_str = str(evidence)[:120] if evidence else "none"
            print(f"\n  conf={r['confidence']:.3f}  evidence: {evidence_str}")
            print(f"  A: {r['src_text'][:100]}")
            print(f"  B: {r['tgt_text'][:100]}")
    await s.close()

asyncio.run(sample())

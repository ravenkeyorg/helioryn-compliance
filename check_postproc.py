# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
import sys, asyncio
sys.path.insert(0, "src")

from helioryn.store import EventStore

async def check():
    s = EventStore("postgresql:///helioryn_dev?host=/tmp")
    await s.connect()
    async with s._pool.acquire() as conn:
        ss = await conn.fetchval("SELECT COUNT(*) FROM source_snapshot")
        cl = await conn.fetchval("SELECT COUNT(*) FROM claim")
        print(f"Snapshots: {ss}, Claims: {cl}")

        # Check if sources without claims exist
        rows = await conn.fetch(
            "SELECT ss.source_id FROM source_snapshot ss "
            "WHERE NOT EXISTS (SELECT 1 FROM claim c WHERE c.source_id = ss.source_id) "
            "LIMIT 3"
        )
        print(f"Sources without claims: {len(rows)}")
        for r in rows:
            print(f"  {r['source_id']}")
    await s.close()

asyncio.run(check())

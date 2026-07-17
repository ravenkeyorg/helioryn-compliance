# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
import asyncio, sys
sys.path.insert(0, "src")
from helioryn.store import EventStore

async def check():
    s = EventStore("postgresql:///helioryn_dev?host=/tmp")
    await s.connect()
    async with s._pool.acquire() as conn:
        unproc = await conn.fetchval("SELECT COUNT(*) FROM source_snapshot ss WHERE NOT EXISTS (SELECT 1 FROM claim c WHERE c.source_id = ss.source_id)")
        total_s = await conn.fetchval("SELECT COUNT(*) FROM source_snapshot")
        total_c = await conn.fetchval("SELECT COUNT(*) FROM claim")
        obs_c = await conn.fetchval("SELECT COUNT(*) FROM claim_observation")
        print(f"Sources: {total_s}, Unprocessed: {unproc}, Claims: {total_c}, Observations: {obs_c}")

        due = await conn.fetchval("SELECT COUNT(*) FROM search_query WHERE last_run IS NULL OR last_run < NOW() - INTERVAL '120 minutes'")
        print(f"Due queries: {due}")
    await s.close()

asyncio.run(check())

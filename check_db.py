# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
import asyncio
from helioryn.store import EventStore

async def check():
    store = EventStore("postgresql:///helioryn_dev?host=/tmp")
    await store.connect()
    async with store._pool.acquire() as conn:
        rows = await conn.fetch("SELECT source_id, source_url, title FROM source_snapshot LIMIT 5")
        for r in rows:
            print(f"  {str(r['source_id'])[:8]}: {str(r['title'])[:60]}")
        ss = await conn.fetchval("SELECT COUNT(*) FROM source_snapshot")
        print(f"Total source snapshots: {ss}")
    await store.close()

asyncio.run(check())

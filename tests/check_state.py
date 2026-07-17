# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
import asyncio, sys
from helioryn.store import EventStore

async def c():
    s = EventStore("postgresql:///helioryn_dev?host=/tmp")
    await s.connect()
    async with s._pool.acquire() as conn:
        ss = await conn.fetchval("SELECT COUNT(*) FROM source_snapshot")
        cl = await conn.fetchval("SELECT COUNT(*) FROM claim")
        em = await conn.fetchval("SELECT COUNT(*) FROM claim_embedding")
        rr = await conn.fetchval("SELECT COUNT(*) FROM claim_relationship")
        ns = await conn.fetchval("SELECT COUNT(*) FROM narrative")
        print(f"SS:{ss} CL:{cl} EM:{em} RR:{rr} NS:{ns}")
    await s.close()

asyncio.run(c())

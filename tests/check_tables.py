# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
import sys, asyncio
sys.path.insert(0, "src")
from helioryn.store import EventStore

async def c():
    s = EventStore("postgresql:///helioryn_dev?host=/tmp")
    await s.connect()
    async with s._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        print("Tables:")
        for r in rows:
            print(f"  {r['table_name']}")

        sq = await conn.fetchval("SELECT COUNT(*) FROM search_query")
        print(f"\nQueries: {sq}")

        due = await conn.fetch(
            "SELECT text FROM search_query WHERE last_run IS NULL LIMIT 5"
        )
        print(f"Due queries (null last_run): {len(due)}")
        for r in due:
            print(f"  {r['text']}")

        due_active = await conn.fetchval(
            "SELECT COUNT(*) FROM search_query WHERE last_run IS NULL AND active = TRUE"
        )
        print(f"Active due queries: {due_active}")
    await s.close()

asyncio.run(c())

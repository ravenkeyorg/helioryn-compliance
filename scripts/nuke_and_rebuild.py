# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
"""
Nuke the database and rebuild from scratch.

Drops ALL tables, recreates schema via ensure_schema(), seeds government
entities, company entities, researcher entities, investor entities, and
inserts curated topic queries from helioryn.toml.

Usage:
    python3 helioryn/scripts/nuke_and_rebuild.py [--config helioryn.toml]
"""

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import tomli

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_CONFIG = PROJECT_ROOT / "helioryn.toml"

DROP_ORDER = [
    "claim_relationship",
    "claim_observation",
    "narrative_claim",
    "narrative",
    "claim_embedding",
    "claim_entity",
    "claim",
    "entity",
    "government_entity",
    "source_snapshot",
    "source_ingested",
    "search_query",
]


def get_dsn(config_path: str | None) -> str:
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG
    if cfg_path.exists():
        with open(cfg_path, "rb") as f:
            config = tomli.load(f)
        db = config.get("database", {})
        if db.get("url"):
            return db["url"]
    return "postgresql://localuser@/localhost_dev?host=/tmp"


async def load_entities(filepath: str) -> list[dict]:
    with open(filepath) as f:
        return json.load(f)


async def insert_topic_queries(conn, config_path: str | None):
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG
    if not cfg_path.exists():
        print("No config file found, skipping topic queries")
        return 0

    with open(cfg_path, "rb") as f:
        config = tomli.load(f)

    items = config.get("ingest", {}).get("topics", {}).get("items", [])
    count = 0
    for item in items:
        await conn.execute(
            """INSERT INTO search_query (text, language, source, priority, interval_m)
               VALUES ($1, $2, 'topic', 5, $3)
               ON CONFLICT (text) DO NOTHING""",
            item["query"],
            item.get("language", "en"),
            item.get("interval_minutes", 720),
        )
        count += 1
    return count


async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "--config" else str(DEFAULT_CONFIG)
    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        if idx + 1 < len(sys.argv):
            config_path = sys.argv[idx + 1]

    dsn = get_dsn(config_path)

    print(f"Connecting to: {dsn}")
    conn = await asyncpg.connect(dsn)

    try:
        # --- Phase 1: Drop all tables ---
        print("\n=== Dropping tables ===")
        for table in DROP_ORDER:
            await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            print(f"  Dropped {table}")

        # --- Phase 2: Recreate schema ---
        print("\n=== Recreating schema ===")
        from helioryn.store import EventStore
        store = EventStore(dsn)
        store._pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        await store.ensure_schema()
        print("  Schema recreated")

        # --- Phase 3: Insert topic queries ---
        print("\n=== Inserting topic queries ===")
        count = await insert_topic_queries(conn, config_path)
        print(f"  {count} topic queries inserted")

        # --- Phase 4: Seed entities ---
        print("\n=== Seeding entities ===")

        seed_files = [
            ("governments.json", "government"),
            ("companies.json", "company"),
            ("researchers.json", "person"),
            ("investors.json", "investor"),
        ]

        total_entities = 0
        for filename, default_type in seed_files:
            filepath = DATA_DIR / filename
            if not filepath.exists():
                print(f"  SKIP {filename} (not found)")
                continue
            entities = await load_entities(str(filepath))
            for entity in entities:
                await conn.execute(
                    """INSERT INTO government_entity
                       (name, entity_type, level, country, region, search_name, aliases, discovered_by)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, 'seed')
                       ON CONFLICT (name) DO UPDATE SET active = TRUE""",
                    entity["name"],
                    entity.get("entity_type", default_type),
                    entity.get("level"),
                    entity.get("country"),
                    entity.get("region"),
                    entity.get("search_name", entity["name"]),
                    entity.get("aliases", []),
                )
            total_entities += len(entities)
            print(f"  {len(entities):3d} {default_type} entities from {filename}")

        print(f"\n  Total entities seeded: {total_entities}")

        # --- Phase 5: Generate entity queries ---
        print("\n=== Generating entity queries ===")
        from helioryn.discovery.entity_db import generate_queries_from_entities
        store._pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        queries = await generate_queries_from_entities(store)
        print(f"  {queries} entity queries generated")

        # --- Phase 6: Report ---
        total_queries = await conn.fetchval("SELECT COUNT(*) FROM search_query")
        print(f"\n=== Done ===")
        print(f"  Entities: {total_entities}")
        print(f"  Queries:  {total_queries}")
        print("  Ready to discover. Run: helioryn discover run --aggressive")

    finally:
        await conn.close()
        if hasattr(store, '_pool') and store._pool:
            await store._pool.close()


if __name__ == "__main__":
    asyncio.run(main())

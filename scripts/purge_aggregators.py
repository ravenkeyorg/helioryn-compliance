# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
"""
Migration script: remove sources from known aggregator/rebroadcast domains.

Deletes in FK-safe order:
  1. claim_relationship
  2. claim_observation
  3. narrative_claim
  4. claim
  5. source_snapshot
  6. source_ingested

Usage:
    python3 helioryn/scripts/purge_aggregators.py
"""

import asyncio
import sys

import asyncpg

AGGREGATOR_DOMAINS = [
    "msn.com",
    "aol.com",
    "yahoo.com",
    "markets.businessinsider.com",
    "247wallst.com",
    "prnewswire.com",
    "cryptobriefing.com",
]

DSN = "postgresql://btaylor@/helioryn_dev?host=/tmp"


def build_matches(domains: list[str]) -> str:
    clauses = [f"source_url LIKE '%{d}%'" for d in domains]
    return " OR ".join(clauses)


async def main():
    conn = await asyncpg.connect(DSN)

    try:
        where = build_matches(AGGREGATOR_DOMAINS)

        # --- Phase 1: Identify targets ---
        agg_ids = await conn.fetch(f"SELECT DISTINCT source_id FROM source_ingested WHERE {where}")
        agg_id_list = [str(r["source_id"]) for r in agg_ids]
        if not agg_id_list:
            print("No matching sources found. Nothing to do.")
            return

        total = len(agg_id_list)
        print(f"Aggregator source_ids: {total}")

        claim_ids = await conn.fetch(
            f"SELECT claim_id FROM claim WHERE source_id = ANY($1::uuid[])", agg_id_list
        )
        claim_id_list = [str(r["claim_id"]) for r in claim_ids]
        print(f"Claims from those sources: {len(claim_id_list)}")

        rels = await conn.fetch(
            "SELECT COUNT(*) as cnt FROM claim_relationship "
            f"WHERE source_claim_id = ANY($1::uuid[]) OR target_claim_id = ANY($1::uuid[])",
            claim_id_list if claim_id_list else [],
        )
        rel_count = rels[0]["cnt"] if rels else 0
        print(f"Relationships involving those claims: {rel_count}")

        obs = await conn.fetch(
            "SELECT COUNT(*) as cnt FROM claim_observation "
            f"WHERE claim_id = ANY($1::uuid[])",
            claim_id_list if claim_id_list else [],
        )
        obs_count = obs[0]["cnt"] if obs else 0
        print(f"Observations for those claims: {obs_count}")

        print(f"\nThis will DELETE {len(claim_id_list)} claims, {rel_count} relationships, "
              f"{obs_count} observations, and {total} source records.")
        print("Proceed? [y/N] ", end="", flush=True)
        answer = sys.stdin.readline().strip().lower()
        if answer != "y":
            print("Aborted.")
            return

        # --- Phase 2: Delete in order ---
        # 1. claim_relationship
        if claim_id_list and rel_count > 0:
            deleted = await conn.execute(
                "DELETE FROM claim_relationship WHERE "
                "source_claim_id = ANY($1::uuid[]) OR target_claim_id = ANY($1::uuid[])",
                claim_id_list,
            )
            print(f"Deleted relationships: {deleted}")

        # 2. claim_observation
        if claim_id_list and obs_count > 0:
            deleted = await conn.execute(
                "DELETE FROM claim_observation WHERE claim_id = ANY($1::uuid[])",
                claim_id_list,
            )
            print(f"Deleted observations: {deleted}")

        # 3. narrative_claim
        deleted = await conn.execute(
            "DELETE FROM narrative_claim WHERE claim_id = ANY($1::uuid[])",
            claim_id_list,
        )
        print(f"Deleted narrative_claim links: {deleted}")

        # 4. claim
        if claim_id_list:
            deleted = await conn.execute(
                "DELETE FROM claim WHERE claim_id = ANY($1::uuid[])",
                claim_id_list,
            )
            print(f"Deleted claims: {deleted}")

        # 5. source_snapshot
        deleted = await conn.execute(
            "DELETE FROM source_snapshot WHERE source_id = ANY($1::uuid[])",
            agg_id_list,
        )
        print(f"Deleted source_snapshots: {deleted}")

        # 6. source_ingested
        deleted = await conn.execute(
            "DELETE FROM source_ingested WHERE source_id = ANY($1::uuid[])",
            agg_id_list,
        )
        print(f"Deleted source_ingested: {deleted}")

        # --- Phase 3: Report ---
        remaining = await conn.fetchval("SELECT COUNT(*) FROM source_ingested")
        sources = await conn.fetchval("SELECT COUNT(DISTINCT source_id) FROM source_ingested")
        claims = await conn.fetchval("SELECT COUNT(*) FROM claim")
        rels = await conn.fetchval("SELECT COUNT(*) FROM claim_relationship")
        print(f"\nRemaining: {remaining} snapshots, {sources} sources, "
              f"{claims} claims, {rels} relationships")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

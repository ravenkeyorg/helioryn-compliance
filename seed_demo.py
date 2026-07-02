#!/usr/bin/env python3
"""Seed demo data with real OVC/VOCA content."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from helioryn.store import EventStore
from helioryn.config import AppConfig


async def main():
    config = AppConfig.load()
    store = EventStore(config.database_url)
    await store.connect()
    try:
        await store.ensure_schema()
    except Exception:
        pass

    from helioryn.ingest.documents.ingest import ingest_document

    demo_dir = os.path.join(os.path.dirname(__file__), "demo-data")
    files = [
        ("ovc-grant-conditions.txt", "OVC Grant Conditions 2026"),
        ("ovc-training.txt", "OVC Training and Technical Assistance"),
        ("vocat-standards.txt", "VOCA Victim Advocate Training Standards"),
        ("voca-grant-requirements.txt", "VOCA Grant Requirements and Compliance Standards"),
        ("sample-training-policy.txt", "Victim Services Training Policy - Alaska Victim Services Program"),
        ("employee-training-records.txt", "Employee Training Records 2026 - Alaska Victim Services Program"),
    ]

    total = 0
    for fname, title in files:
        path = os.path.join(demo_dir, fname)
        if not os.path.exists(path):
            print(f"  SKIP {fname} (not found)")
            continue
        result = await ingest_document(store, path, title=title)
        n = result.get("ingested", 0)
        total += n
        print(f"  {fname}: {n} chunks ingested")

    print(f"\nTotal new chunks ingested: {total}")

    # Verify
    src_count = await store.execute("SELECT COUNT(*) FROM source_snapshot WHERE retrieval_method = 'upload'")
    clm_count = await store.get_claim_count()
    emb_count = await store.get_embedding_count()
    src_rows = int(src_count.split()[-1]) if isinstance(src_count, str) else src_count
    print(f"Uploaded sources: {src_rows}, Total claims: {clm_count}, Embeddings: {emb_count}")

    await store.close()


if __name__ == "__main__":
    asyncio.run(main())

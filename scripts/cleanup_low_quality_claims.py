# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
"""
One-time cleanup: delete low-quality claims from the database.

Usage on M4:
  /Users/btaylor/Projects/helioryn-design/helioryn/venv/bin/python \
  /tmp/cleanup_low_quality_claims.py
"""

import asyncio
import re
import sys
from pathlib import Path

# ── Read DB URL from helioryn.toml ──────────────────────────────────
_CONFIG_PATH = Path("/Users/btaylor/Projects/helioryn-design/helioryn.toml")
try:
    import tomllib
except ImportError:
    import tomli as tomllib

with open(_CONFIG_PATH, "rb") as f:
    _cfg = tomllib.load(f)
DATABASE_URL = _cfg["database"]["url"]

# ── Pattern set (mirrors extract/__init__.py + store.py) ─────────────
_BOILERPLATE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(?:retrieved|accessed)\s+\w+", re.IGNORECASE),
    re.compile(r"^doi:\s*10\.", re.IGNORECASE),
    re.compile(r"^isbn:\s*\d", re.IGNORECASE),
    re.compile(r"^join the conversation", re.IGNORECASE),
    re.compile(r"^©|copyright", re.IGNORECASE),
    re.compile(r"^all\s+(?:rights\s+)?reserved", re.IGNORECASE),
    re.compile(r"^https?://", re.IGNORECASE),
    re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE),
    re.compile(r"^\s*$"),
    re.compile(r"^world economic forum\.?$", re.IGNORECASE),
    re.compile(r"^plays?\s+of\s+the", re.IGNORECASE),
    re.compile(r"^(?:sign\s+(?:in|up|out)|subscribe|click\s+here|read\s+more|learn\s+more|contact\s+us|privacy\s+policy|terms\s+of\s+service|cookie|share\s+this|follow\s+us|join\s+the\s+conversation)", re.IGNORECASE),
    re.compile(r"visit\s+(?:the|our)\s", re.IGNORECASE),
    re.compile(r"\[\d+\]|\[edit\]|footnote", re.IGNORECASE),
    re.compile(r"^\d+\s+words?$", re.IGNORECASE),
    re.compile(r"^\w+\s+\^\s+\w+"),
]


def _is_low_quality(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    for pat in _BOILERPLATE_PATTERNS:
        if pat.search(stripped):
            return True
    if len(stripped) < 60:
        return not bool(re.search(r"[A-Z][a-z]{3,}", stripped))
    if len(stripped) > 5000:
        return True
    return False


async def main():
    import asyncpg

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        ids_to_delete = []
        total = 0
        offset = 0
        batch_size = 5000

        while True:
            rows = await conn.fetch(
                "SELECT claim_id, canonical_text FROM claim "
                "ORDER BY claim_id LIMIT $1 OFFSET $2",
                batch_size, offset,
            )
            if not rows:
                break
            offset += len(rows)
            total += len(rows)
            bad = [r["claim_id"] for r in rows if _is_low_quality(r["canonical_text"] or "")]
            if bad:
                ids_to_delete.extend(bad)
                print(f"  batch {total}: {len(bad)} low-quality claims", flush=True)
            if len(rows) < batch_size:
                break

        print(f"\nScanned {total} claims. Found {len(ids_to_delete)} to delete.")
        if not ids_to_delete:
            print("Nothing to clean up.")
            return

        # Preview samples
        sample = await conn.fetch(
            "SELECT claim_id, canonical_text FROM claim WHERE claim_id = ANY($1::uuid[]) LIMIT 10",
            ids_to_delete[:10],
        )
        print("\nSample garbage:")
        for r in sample:
            text = (r["canonical_text"] or "")[:120]
            print(f"  {str(r['claim_id'])[:8]}... {text}")

        print(f"\nProceed? [{len(ids_to_delete)} claims] [y/N] ", end="", flush=True)
        resp = sys.stdin.readline().strip().lower()
        if resp != "y":
            print("Aborted.")
            return

        # Clean NO ACTION constraint tables first
        result = await conn.execute(
            "DELETE FROM claim_mutation WHERE source_claim_id = ANY($1::uuid[]) OR target_claim_id = ANY($1::uuid[])",
            ids_to_delete,
        )
        print(f"  claim_mutation: {result}")
        result = await conn.execute(
            "DELETE FROM external_factcheck WHERE claim_id = ANY($1::uuid[])",
            ids_to_delete,
        )
        print(f"  external_factcheck: {result}")

        # Delete claims (CASCADE handles narrative_claim, embedding, grounding, relationship, version)
        result = await conn.execute(
            "DELETE FROM claim WHERE claim_id = ANY($1::uuid[])",
            ids_to_delete,
        )
        print(f"  claim: {result}")

        print(f"\nDone. Removed {len(ids_to_delete)} low-quality claims.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

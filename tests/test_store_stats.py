# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from uuid import uuid4

import pytest

from helioryn.config import AppConfig
from helioryn.store import EventStore


@pytest.fixture
def config():
    return AppConfig.load()


@pytest.mark.asyncio
async def test_get_stats_returns_keys(config):
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()
    stats = await store.get_stats()
    assert "total_sources" in stats
    assert "total_events" in stats
    assert "updated_sources" in stats
    assert "oldest_source" in stats
    assert "newest_source" in stats
    await store.close()


@pytest.mark.asyncio
async def test_upsert_claim_entity_type_votes(config):
    """Upsert same entity name with different types, verify type_votes tracks conflicts."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    name = f"__test_type_votes_{uuid4().hex[:8]}"

    # First upsert: person
    eid1 = await store.upsert_claim_entity(name, "person")
    assert eid1 is not None

    # Second upsert: location (should conflict, track vote)
    eid2 = await store.upsert_claim_entity(name, "location")
    assert eid2 == eid1  # same entity_id

    # Third upsert: organization
    eid3 = await store.upsert_claim_entity(name, "organization")
    assert eid3 == eid1

    # Verify original type preserved (person, not overwritten by location)
    async with store._pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT entity_type, type_votes FROM entity WHERE name = $1", name
        )
    assert row is not None
    assert row["entity_type"] == "person"  # original type preserved

    # Verify type_votes tracks conflicting types
    assert row["type_votes"] is not None
    tv = dict(row["type_votes"])
    assert "location" in tv
    assert "organization" in tv
    assert tv["location"] >= 1
    assert tv["organization"] >= 1

    # Cleanup
    async with store._pool.acquire() as conn:
        await conn.execute("DELETE FROM entity WHERE name = $1", name)
    await store.close()

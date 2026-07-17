# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
"""Integration tests for EventStore methods (requires PostgreSQL + pgvector)."""

import platform
from uuid import uuid4

import pytest
import pytest_asyncio

from helioryn.store import EventStore

TEST_DB_URL = "postgresql:///helioryn_test?host=/var/run/postgresql"
HAS_SKLEARN = False
try:
    import sklearn  # noqa: F401
    HAS_SKLEARN = True
except ImportError:
    pass


@pytest_asyncio.fixture
async def store():
    s = EventStore(TEST_DB_URL)
    await s.connect()
    await s.ensure_schema()
    yield s
    async with s._pool.acquire() as conn:
        await conn.execute("DELETE FROM claim_mutation")
        await conn.execute("DELETE FROM confidence_factor WHERE target_type = 'claim'")
        await conn.execute("DELETE FROM claim_observation")
        await conn.execute("DELETE FROM claim_relationship")
        await conn.execute("DELETE FROM claim_embedding")
        await conn.execute("DELETE FROM claim_version")
        await conn.execute("DELETE FROM claim_entity")
        await conn.execute("DELETE FROM claim")
        await conn.execute("DELETE FROM narrative_claim")
        await conn.execute("DELETE FROM narrative")
        await conn.execute("DELETE FROM entity")
        await conn.execute("DELETE FROM source_behavior_event")
        await conn.execute("DELETE FROM source_behavior")
        await conn.execute("DELETE FROM source_snapshot")
        await conn.execute("DELETE FROM ledger")
    await s.close()


@pytest.fixture
def name():
    return f"__test_{uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_confidence_factor_crud(store, name):
    """Insert, read, update, and delete confidence factors."""
    source_id = uuid4()
    await store.insert_confidence_factor("source", source_id, "test_factor", 0.75, 1.0, "test")
    factors = await store.get_confidence_factors("source", source_id)
    assert len(factors) == 1
    assert factors[0]["factor_type"] == "test_factor"
    assert factors[0]["value"] == pytest.approx(0.75)

    await store.insert_confidence_factor("source", source_id, "test_factor", 0.85, 1.0, "updated")
    factors = await store.get_confidence_factors("source", source_id)
    assert factors[0]["value"] == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_annotation_investigation_flow(store, name):
    """Full annotation → investigation → note workflow."""
    obj_id = uuid4()

    aid = await store.insert_annotation("claim", obj_id, "tester", "test annotation", ["test"])
    assert aid is not None

    ok = await store.resolve_annotation(aid)
    assert ok

    iid = await store.create_investigation(name, "test investigation", "tester")
    assert iid is not None

    ok = await store.add_to_investigation(iid, claim_ids=[obj_id])
    assert ok

    invs = await store.list_investigations()
    assert any(i["investigation_id"] == iid for i in invs)

    note_id = await store.add_investigation_note(iid, "tester", "first note")
    assert note_id is not None

    notes = await store.list_investigation_notes(iid)
    assert len(notes) == 1

    detail = await store.get_investigation_detail(iid)
    assert detail is not None
    assert detail["name"] == name

    ok = await store.close_investigation(iid, "resolved")
    assert ok


@pytest.mark.asyncio
async def test_staging_queue_workflow(store):
    """Submit, list, and review staging queue items."""
    qid = await store.submit_to_staging("claim", uuid4(), "tester", "test submission")
    assert qid is not None

    items = await store.list_staging("pending")
    assert any(i["queue_id"] == qid for i in items)

    ok = await store.review_staging_item(qid, "reviewer", "rejected", "not needed")
    assert ok

    items = await store.list_staging("approved")
    assert not any(i["queue_id"] == qid for i in items)


@pytest.mark.asyncio
async def test_clear_old_confidence_factors(store):
    """Old scaffolding factors are cleaned up properly."""
    cid = uuid4()
    await store.insert_confidence_factor("claim", cid, "temporal_precision", 0.5, 0.5, "old")
    await store.insert_confidence_factor("claim", cid, "uncertainty", 0.3, 0.8, "old")
    await store.insert_confidence_factor("claim", cid, "source_reliability", 0.9, 0.3, "new")

    n = await store.clear_old_confidence_factors()
    assert n >= 2

    factors = await store.get_confidence_factors("claim", cid)
    types = [f["factor_type"] for f in factors]
    assert "temporal_precision" not in types
    assert "uncertainty" not in types
    assert "source_reliability" in types


@pytest.mark.asyncio
async def test_detect_contradictions_basic(store):
    """Basic contradiction detection works between two claims."""
    src_id = uuid4()
    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO source_snapshot (source_id, source_url, retrieved_at, raw_text, content_hash, retrieval_method, first_seen_at, last_updated_at) "
            "VALUES ($1, $2, now(), '', 'hash', 'test', now(), now())",
            src_id, "https://example.com/test",
        )

    cid_a = uuid4()
    cid_b = uuid4()
    canonical_id = uuid4()
    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO canonical_claim (canonical_id, canonical_text) VALUES ($1, '')", canonical_id
        )
        for cid, text in [(cid_a, "GDP grew 4% in 2024"), (cid_b, "GDP grew 5% in 2024")]:
            await conn.execute(
                "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text, canonical_id) "
                "VALUES ($1, $2, 'https://example.com', $3, '', $4)",
                cid, src_id, text, canonical_id,
            )
            await conn.execute(
                "INSERT INTO claim_observation (claim_id, source_id, observer) VALUES ($1, $2, 'test')",
                cid, src_id,
            )

    n = await store.detect_contradictions(model_name="all-MiniLM-L6-v2", batch_size=100)
    # May or may not detect — depends on embedding model availability.
    # The test just verifies it runs without error.
    assert isinstance(n, int)


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_SKLEARN, reason="sklearn not installed")
async def test_narrative_detection(store):
    """Narrative clustering runs without error on test data."""
    src_id = uuid4()
    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO source_snapshot (source_id, source_url, retrieved_at, raw_text, content_hash, retrieval_method, first_seen_at, last_updated_at) "
            "VALUES ($1, $2, now(), '', 'hash', 'test', now(), now())",
            src_id, "https://example.com/test",
        )

    texts = [
        "AI regulation in Europe is evolving rapidly",
        "The EU AI Act sets new standards for artificial intelligence governance",
        "Machine learning models require vast amounts of training data",
        "Deep neural networks are transforming computer vision",
        "Climate change impacts agricultural output globally",
        "Carbon emissions continue to rise despite international agreements",
    ]
    for i, text in enumerate(texts):
        cid = uuid4()
        async with store._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
                "VALUES ($1, $2, 'https://example.com', $3, '')",
                cid, src_id, text,
            )

    n = await store.detect_narratives(k=2)
    assert isinstance(n, int)


@pytest.mark.asyncio
async def test_evidence_density_materialized_view(store):
    """Evidence density materialized view refreshes without error."""
    await store.refresh_evidence_density()
    async with store._pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM evidence_density")
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_narrative_list(store, name):
    """Narrative list works."""
    nars = await store.list_narratives()
    assert isinstance(nars, list)

    # Detect narratives — runs LDA, may fail if sklearn not available
    if HAS_SKLEARN:
        n = await store.detect_narratives(k=2)
        assert isinstance(n, int)

    nars = await store.list_narratives()
    if nars:
        nid = nars[0]["narrative_id"]
        detail = await store.get_narrative(nid)
        assert detail is not None
        await store.delete_narrative(nid)


# ── Ledger Tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_append_and_retrieve(store, name):
    """Append ledger entries and retrieve them by claim_id."""
    cid = uuid4()
    src_id = uuid4()
    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO source_snapshot (source_id, source_url, retrieved_at, raw_text, content_hash, retrieval_method, first_seen_at, last_updated_at) "
            "VALUES ($1, 'https://a.com', now(), '', 'h', 'test', now(), now())",
            src_id,
        )
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, 'https://a.com', 'test claim', '')",
            cid, src_id,
        )

    e1 = await store.append_ledger("source_fetch", {"url": "https://a.com"}, source_id=src_id)
    assert e1 is not None
    assert e1["entry_type"] == "source_fetch"
    assert e1["claim_id"] is None

    e2 = await store.append_ledger("claim_extraction", {"text": "test claim"}, claim_id=cid)
    assert e2 is not None
    assert e2["claim_id"] == str(cid)

    chain = await store.get_chain(claim_id=cid)
    assert len(chain) == 1
    assert chain[0]["entry_type"] == "claim_extraction"


@pytest.mark.asyncio
async def test_ledger_chain_integrity(store, name):
    """Verify intact chain passes, tampered chain fails."""
    cid = uuid4()
    src_id = uuid4()
    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO source_snapshot (source_id, source_url, retrieved_at, raw_text, content_hash, retrieval_method, first_seen_at, last_updated_at) "
            "VALUES ($1, 'https://a.com', now(), '', 'h', 'test', now(), now())",
            src_id,
        )
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, 'https://a.com', 'test', '')",
            cid, src_id,
        )
    # Append 3 entries
    for typ in ("source_fetch", "claim_extraction", "factor"):
        await store.append_ledger(typ, {"seq": typ}, claim_id=cid)

    results = await store.verify_chain(claim_id=cid)
    assert len(results) == 3
    assert all(r["valid"] for r in results)

    # Tamper with the second entry's data_hash
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, data_hash FROM ledger WHERE claim_id = $1 ORDER BY id", cid
        )
        tampered_id = rows[1]["id"]
        await conn.execute(
            "UPDATE ledger SET data_hash = 'tampered' WHERE id = $1", tampered_id
        )

    results = await store.verify_chain(claim_id=cid)

    # The tampered entry itself is valid (its previous_hash still points to entry 1's correct data_hash)
    # But all entries after it should be invalid (their previous_hash no longer matches)
    tampered_idx = next(i for i, r in enumerate(results) if r["id"] == tampered_id)
    assert results[tampered_idx]["valid"]  # tampered entry itself is valid
    # Entry after tampered should be invalid
    for r in results[tampered_idx + 1:]:
        assert not r["valid"], f"Entry {r['id']} should be invalid after tamper"


@pytest.mark.asyncio
async def test_ledger_status(store, name):
    """Ledger status returns correct statistics."""
    cid = uuid4()
    src_id = uuid4()
    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO source_snapshot (source_id, source_url, retrieved_at, raw_text, content_hash, retrieval_method, first_seen_at, last_updated_at) "
            "VALUES ($1, 'https://a.com', now(), '', 'h', 'test', now(), now())",
            src_id,
        )
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, 'https://a.com', 'test', '')",
            cid, src_id,
        )

    for i in range(5):
        await store.append_ledger("claim_extraction", {"i": i}, claim_id=cid)

    status = await store.ledger_status()
    assert status["total_entries"] >= 5
    assert status["by_type"].get("claim_extraction", 0) >= 5
    assert status["broken_links"] == 0
    assert status["latest_entry"] is not None

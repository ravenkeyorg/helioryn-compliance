# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from uuid import uuid4
from datetime import datetime, timezone, timedelta

import pytest

from helioryn.config import AppConfig
from helioryn.store import EventStore
from helioryn.models import SourceEvent, Observation, ClaimRelationship


TEST_PREFIX = "__test_conf_"


@pytest.fixture
def config():
    return AppConfig.load()


def _test_id() -> str:
    return f"{TEST_PREFIX}{uuid4().hex[:8]}"


async def _cleanup_confidence(store: EventStore, pattern: str):
    async with store._pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM claim_relationship WHERE evidence LIKE $1", f"%{pattern}%"
        )
        await conn.execute(
            "DELETE FROM confidence_factor WHERE explanation LIKE $1", f"%{pattern}%"
        )
        await conn.execute(
            "DELETE FROM claim_observation WHERE observer = $1", pattern
        )
        await conn.execute(
            "DELETE FROM claim_embedding WHERE claim_id IN "
            "(SELECT claim_id FROM claim WHERE source_url LIKE $1)",
            f"%{pattern}%",
        )
        await conn.execute(
            "DELETE FROM claim_entity WHERE claim_id IN "
            "(SELECT claim_id FROM claim WHERE source_url LIKE $1)",
            f"%{pattern}%",
        )
        await conn.execute(
            "DELETE FROM source_behavior_event WHERE claim_id IN "
            "(SELECT claim_id FROM claim WHERE source_url LIKE $1)",
            f"%{pattern}%",
        )
        await conn.execute(
            "DELETE FROM source_behavior_event WHERE detail LIKE $1", f"%{pattern}%"
        )
        await conn.execute(
            "DELETE FROM claim WHERE source_url LIKE $1", f"%{pattern}%"
        )
        await conn.execute(
            "DELETE FROM source_behavior WHERE source_id IN "
            "(SELECT source_id FROM source_ingested WHERE source_url LIKE $1)",
            f"%{pattern}%",
        )
        await conn.execute(
            "DELETE FROM source_ingested WHERE source_url LIKE $1", f"%{pattern}%"
        )


@pytest.mark.asyncio
async def test_update_source_behavior_claims(config):
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_url = f"https://example.com/{test_id}/source"
    source_id = uuid4()

    event = SourceEvent(
        source_id=source_id,
        source_url=source_url,
        title=f"Test Source {test_id}",
        author="Test Author",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Test content for source behavior test.",
        raw_html="<html><body>Test content</body></html>",
        content_hash=f"hash_{test_id}",
        metadata={},
        retrieval_method="test",
    )
    await store.append_event(event)

    await store.update_source_behavior(source_id, {"n_claims": 5})
    behavior = await store.get_source_behavior(source_id)
    assert behavior is not None
    assert behavior["n_claims"] == 5

    await store.update_source_behavior(source_id, {"n_claims": 3})
    behavior = await store.get_source_behavior(source_id)
    assert behavior["n_claims"] == 8

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_update_source_behavior_delta(config):
    """Verify multiple delta types accumulate independently."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_url = f"https://example.com/{test_id}/delta"
    source_id = uuid4()

    event = SourceEvent(
        source_id=source_id,
        source_url=source_url,
        title=f"Test Delta {test_id}",
        author="Test Author",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Test content for delta test.",
        raw_html="<html><body>Test</body></html>",
        content_hash=f"hash_delta_{test_id}",
        metadata={},
        retrieval_method="test",
    )
    await store.append_event(event)

    await store.update_source_behavior(source_id, {
        "n_claims": 10, "n_contradictions": 2, "n_original_claims": 8, "n_repeated_claims": 2
    })
    await store.update_source_behavior(source_id, {
        "n_claims": 5, "n_contradictions": 1, "n_original_claims": 3, "n_repeated_claims": 2
    })
    behavior = await store.get_source_behavior(source_id)
    assert behavior["n_claims"] == 15
    assert behavior["n_contradictions"] == 3
    assert behavior["n_original_claims"] == 11
    assert behavior["n_repeated_claims"] == 4

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_source_reliability_score(config):
    """High contradiction rate → low reliability score."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_url = f"https://example.com/{test_id}/reliability"
    source_id = uuid4()

    event = SourceEvent(
        source_id=source_id,
        source_url=source_url,
        title=f"Test Reliability {test_id}",
        author="Test Author",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Test content for reliability scoring.",
        raw_html="<html><body>Test</body></html>",
        content_hash=f"hash_rel_{test_id}",
        metadata={},
        retrieval_method="test",
    )
    await store.append_event(event)

    claim_id_1 = uuid4()
    claim_id_2 = uuid4()

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id_1, source_id, source_url, f"Claim one {test_id}", f"Claim one {test_id}",
        )
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id_2, source_id, source_url, f"Claim two {test_id}", f"Claim two {test_id}",
        )

    for cid in [claim_id_1, claim_id_2]:
        obs = Observation(claim_id=cid, source_id=source_id, observer=test_id)
        await store.insert_observation(obs)

    rel = ClaimRelationship(
        source_claim_id=claim_id_1,
        target_claim_id=claim_id_2,
        relationship_type="contradicts",
        confidence=0.8,
        detected_by="test",
        evidence=f"test contradiction {test_id}",
    )
    await store.insert_relationship(rel)

    await store.update_source_behavior(source_id, {"n_claims": 2, "n_contradictions": 1})
    await store.compute_source_reliability(source_id)

    reliability = await store.get_source_reliability(source_id)
    assert 0.0 <= reliability <= 1.0

    factors = await store.get_confidence_factors("source", source_id)
    factor_types = [f["factor_type"] for f in factors]
    assert "source_reliability" in factor_types

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_source_age_score_old_source(config):
    """Old source with past observations should have high age score (≥ 0.8)."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_url = f"https://example.com/{test_id}/old"
    source_id = uuid4()
    claim_id = uuid4()

    event = SourceEvent(
        source_id=source_id,
        source_url=source_url,
        title=f"Test Old Source {test_id}",
        author="Test Author",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Old source test content.",
        raw_html="<html><body>Old</body></html>",
        content_hash=f"hash_old_{test_id}",
        metadata={},
        retrieval_method="test",
    )
    await store.append_event(event)

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id, source_id, source_url, f"Old claim {test_id}", f"Old claim {test_id}",
        )

    past_obs = Observation(
        claim_id=claim_id,
        source_id=source_id,
        observed_at=datetime.now(timezone.utc) - timedelta(days=90),
        observer=test_id,
    )
    await store.insert_observation(past_obs)

    age_days = await store._get_source_age_days(source_id)
    assert age_days >= 1

    reliability = await store.compute_source_reliability(source_id)
    assert reliability > 0.0

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_classify_claim_originality_original(config):
    """Claim with no similar existing claims should be classified as original."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_url = f"https://example.com/{test_id}/original"
    source_id = uuid4()
    claim_id = uuid4()

    event = SourceEvent(
        source_id=source_id,
        source_url=source_url,
        title=f"Test Original {test_id}",
        author="Test Author",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Unique content that has never appeared before.",
        raw_html="<html><body>Unique</body></html>",
        content_hash=f"hash_orig_{test_id}",
        metadata={},
        retrieval_method="test",
    )
    await store.append_event(event)

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id, source_id, source_url,
            f"Original claim text that is unique {test_id}",
            f"Original claim text that is unique {test_id}",
        )

    result = await store.classify_claim_originality(claim_id, source_id, threshold=0.85)
    assert result["is_original"] is True
    assert result["match_claim_id"] is None

    behavior = await store.get_source_behavior(source_id)
    if behavior:
        assert behavior.get("n_original_claims", 0) == 1

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_contradiction_rate_query(config):
    """get_source_contradiction_rate returns expected ratio."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_url = f"https://example.com/{test_id}/contra_rate"
    source_id = uuid4()

    event = SourceEvent(
        source_id=source_id,
        source_url=source_url,
        title=f"Contra Rate {test_id}",
        author="Test Author",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Test content for contradiction rate.",
        raw_html="<html><body>Test</body></html>",
        content_hash=f"hash_cr_{test_id}",
        metadata={},
        retrieval_method="test",
    )
    await store.append_event(event)

    claim_id_1 = uuid4()
    claim_id_2 = uuid4()

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id_1, source_id, source_url, f"CR claim A {test_id}", f"CR claim A {test_id}",
        )
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id_2, source_id, source_url, f"CR claim B {test_id}", f"CR claim B {test_id}",
        )

    rate = await store.get_source_contradiction_rate(source_id)
    assert rate == 0.0

    rel = ClaimRelationship(
        source_claim_id=claim_id_1,
        target_claim_id=claim_id_2,
        relationship_type="contradicts",
        confidence=0.8,
        detected_by="test",
        evidence=f"CR test {test_id}",
    )
    await store.insert_relationship(rel)

    rate = await store.get_source_contradiction_rate(source_id)
    assert rate > 0.0

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_log_and_get_source_behavior_events(config):
    """Logging events and retrieving them works."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_url = f"https://example.com/{test_id}/events"
    source_id = uuid4()

    event = SourceEvent(
        source_id=source_id,
        source_url=source_url,
        title=f"Test Events {test_id}",
        author="Test Author",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Events test.",
        raw_html="<html><body>Events</body></html>",
        content_hash=f"hash_ev_{test_id}",
        metadata={},
        retrieval_method="test",
    )
    await store.append_event(event)

    await store.log_source_behavior_event(source_id, "original", detail=f"Test original {test_id}")
    await store.log_source_behavior_event(source_id, "correction", detail=f"Test correction {test_id}")

    events = await store.get_source_behavior_events(source_id)
    matching = [e for e in events if test_id in (e.get("detail") or "")]
    assert len(matching) == 2
    assert matching[0]["event_type"] == "correction" or matching[1]["event_type"] == "correction"

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_detect_source_contradictions(config):
    """Same-source contradictions are detected and logged."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_url = f"https://example.com/{test_id}/self_contra"
    source_id = uuid4()

    event = SourceEvent(
        source_id=source_id,
        source_url=source_url,
        title=f"Self Contra {test_id}",
        author="Test Author",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Self contradiction test.",
        raw_html="<html><body>Test</body></html>",
        content_hash=f"hash_sc_{test_id}",
        metadata={},
        retrieval_method="test",
    )
    await store.append_event(event)

    claim_id_1 = uuid4()
    claim_id_2 = uuid4()

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id_1, source_id, source_url, f"SC A {test_id}", f"SC A {test_id}",
        )
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id_2, source_id, source_url, f"SC B {test_id}", f"SC B {test_id}",
        )

    rel = ClaimRelationship(
        source_claim_id=claim_id_1,
        target_claim_id=claim_id_2,
        relationship_type="contradicts",
        confidence=0.8,
        detected_by="test",
        evidence=f"Self contra test {test_id}",
    )
    await store.insert_relationship(rel)

    n = await store.detect_source_contradictions(source_id)
    assert n >= 1

    events = await store.get_source_behavior_events(source_id)
    contra_events = [e for e in events if e["event_type"] == "contradiction"]
    assert len(contra_events) >= 1

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_compute_evidence_diversity(config):
    """Evidence diversity should increase with more sources."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_id = uuid4()
    claim_id = uuid4()

    source_url = f"https://example.com/{test_id}/evdiv"
    event = SourceEvent(
        source_id=source_id, source_url=source_url,
        title=f"EvDiv {test_id}", author="Test",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Test content for evidence diversity.",
        raw_html="<html><body>Test</body></html>",
        content_hash=f"hash_evdiv_{test_id}",
        metadata={}, retrieval_method="test",
    )
    await store.append_event(event)

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id, source_id, source_url, f"EvDiv claim {test_id}", f"EvDiv claim {test_id}",
        )

    obs = Observation(claim_id=claim_id, source_id=source_id, observer=test_id)
    await store.insert_observation(obs)

    score = await store.compute_evidence_diversity(claim_id)
    assert 0.0 <= score <= 1.0

    factors = await store.get_confidence_factors("claim", claim_id)
    has_ev_div = any(f["factor_type"] == "evidence_diversity" for f in factors)
    assert has_ev_div

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_compute_temporal_stability(config):
    """Temporal stability should reflect claim age and observation count."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_id = uuid4()
    claim_id = uuid4()

    source_url = f"https://example.com/{test_id}/tempstab"
    event = SourceEvent(
        source_id=source_id, source_url=source_url,
        title=f"TempStab {test_id}", author="Test",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Test content for temporal stability.",
        raw_html="<html><body>Test</body></html>",
        content_hash=f"hash_tempstab_{test_id}",
        metadata={}, retrieval_method="test",
    )
    await store.append_event(event)

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id, source_id, source_url,
            f"TempStab claim {test_id}", f"TempStab claim {test_id}",
        )

    past_date = datetime.now(timezone.utc) - timedelta(days=30)
    obs = Observation(claim_id=claim_id, source_id=source_id, observed_at=past_date, observer=test_id)
    await store.insert_observation(obs)

    score = await store.compute_temporal_stability(claim_id)
    assert 0.0 <= score <= 1.0

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_compute_contradiction_impact_no_contra(config):
    """Claims with no contradictions should have impact=1.0."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_id = uuid4()
    claim_id = uuid4()

    source_url = f"https://example.com/{test_id}/contraimp"
    event = SourceEvent(
        source_id=source_id, source_url=source_url,
        title=f"ContraImp {test_id}", author="Test",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Test content for contradiction impact.",
        raw_html="<html><body>Test</body></html>",
        content_hash=f"hash_contraimp_{test_id}",
        metadata={}, retrieval_method="test",
    )
    await store.append_event(event)

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id, source_id, source_url,
            f"ContraImp claim {test_id}", f"ContraImp claim {test_id}",
        )

    impact = await store.compute_contradiction_impact(claim_id)
    assert impact == 1.0

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_claim_confidence_composite(config):
    """Composite confidence should be the weighted average of factors."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_id = uuid4()
    claim_id = uuid4()

    source_url = f"https://example.com/{test_id}/composite"
    event = SourceEvent(
        source_id=source_id, source_url=source_url,
        title=f"Composite {test_id}", author="Test",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Test content for composite confidence.",
        raw_html="<html><body>Test</body></html>",
        content_hash=f"hash_comp_{test_id}",
        metadata={}, retrieval_method="test",
    )
    await store.append_event(event)

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text, extraction_method) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            claim_id, source_id, source_url,
            f"Composite claim {test_id}", f"Composite claim {test_id}", "spacy_sentencizer",
        )

    obs = Observation(claim_id=claim_id, source_id=source_id, observed_at=datetime.now(timezone.utc), observer=test_id)
    await store.insert_observation(obs)

    result = await store.compute_claim_confidence(claim_id)
    assert "composite" in result
    assert "factors" in result
    assert 0.0 <= result["composite"] <= 1.0
    assert len(result["factors"]) >= 1

    await _cleanup_confidence(store, test_id)
    await store.close()


@pytest.mark.asyncio
async def test_extraction_method_confidence_map(config):
    """Extraction method maps to expected confidence values."""
    store = EventStore(config.database_url)
    await store.connect()

    assert store._extraction_method_confidence("sentence_split") == 0.7
    assert store._extraction_method_confidence("spacy_sentencizer") == 0.8
    assert store._extraction_method_confidence("llm_extraction") == 0.95
    assert store._extraction_method_confidence("manual") == 1.0
    assert store._extraction_method_confidence("unknown_method") == 0.7

    await store.close()


@pytest.mark.asyncio
async def test_detect_source_corrections(config):
    """Same-source entity pair where later claim contradicts earlier should be a correction."""
    store = EventStore(config.database_url)
    await store.connect()
    await store.ensure_schema()

    test_id = _test_id()
    source_id = uuid4()

    source_url = f"https://example.com/{test_id}/corr"
    event = SourceEvent(
        source_id=source_id, source_url=source_url,
        title=f"Corr {test_id}", author="Test",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Correction test content.",
        raw_html="<html><body>Test</body></html>",
        content_hash=f"hash_corr_{test_id}",
        metadata={}, retrieval_method="test",
    )
    await store.append_event(event)

    claim_id_1 = uuid4()
    claim_id_2 = uuid4()
    entity_id = uuid4()
    entity_name = f"__test_entity_{test_id}"

    async with store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id_1, source_id, source_url, f"Earlier {test_id}", f"Earlier {test_id}",
        )
        await conn.execute(
            "INSERT INTO claim (claim_id, source_id, source_url, canonical_text, original_text) "
            "VALUES ($1, $2, $3, $4, $5)",
            claim_id_2, source_id, source_url, f"Later {test_id}", f"Later {test_id}",
        )
        await conn.execute(
            "INSERT INTO entity (entity_id, name, entity_type) VALUES ($1, $2, 'organization') "
            "ON CONFLICT (name) DO NOTHING",
            entity_id, entity_name,
        )
        await conn.execute(
            "INSERT INTO claim_entity (claim_id, entity_id, mention) VALUES ($1, $2, $3)",
            claim_id_1, entity_id, entity_name,
        )
        await conn.execute(
            "INSERT INTO claim_entity (claim_id, entity_id, mention) VALUES ($1, $2, $3)",
            claim_id_2, entity_id, entity_name,
        )

    past_date = datetime.now(timezone.utc) - timedelta(days=10)
    obs1 = Observation(claim_id=claim_id_1, source_id=source_id, observed_at=past_date, observer=test_id)
    obs2 = Observation(claim_id=claim_id_2, source_id=source_id, observed_at=datetime.now(timezone.utc), observer=test_id)
    await store.insert_observation(obs1)
    await store.insert_observation(obs2)

    rel = ClaimRelationship(
        source_claim_id=claim_id_2,
        target_claim_id=claim_id_1,
        relationship_type="contradicts",
        confidence=0.8,
        detected_by="test",
        evidence=f"Correction test {test_id}",
    )
    await store.insert_relationship(rel)

    n = await store.detect_source_corrections(source_id)
    assert n >= 1, "Should detect at least one self-correction"

    events = await store.get_source_behavior_events(source_id)
    corr_events = [e for e in events if e["event_type"] == "correction"]
    assert len(corr_events) >= 1

    await _cleanup_confidence(store, test_id)
    await store.close()

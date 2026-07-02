# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
"""
End-to-end pipeline verification with synthetic test data.
Inserts known claims with designed contradictions, runs enrichment and
contradiction detection, and verifies the outputs match expectations.

Usage: python tests/verify_pipeline.py [--db DSN]

Requires a running PostgreSQL with the helioryn schema.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from uuid import UUID, uuid4

sys.path.insert(0, "src")

from helioryn.store import EventStore
from helioryn.models import Claim
from helioryn.extract.temporal import extract_temporal_references
from helioryn.extract.uncertainty import detect_uncertainty

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}: {detail}")


async def verify():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="postgresql:///helioryn_dev?host=/tmp")
    args = parser.parse_args()

    store = EventStore(args.db)
    await store.connect()
    await store.ensure_schema()
    print(f"Connected. Schema ready.")

    # Create a synthetic source
    src_id = uuid4()
    src_url = "https://synthetic-test.helioryn.local/test-001"
    from helioryn.models import SourceEvent, SourceSnapshot
    event = SourceEvent(
        event_id=uuid4(), source_id=src_id, source_url=src_url,
        title="Synthetic Verification Source",
        author="Verification Bot",
        publish_date=datetime.now(timezone.utc),
        retrieved_at=datetime.now(timezone.utc),
        raw_text="Verification source for algorithm testing.",
        raw_html=None, content_hash="verify-001-hash",
        metadata={"language": "en"}, retrieval_method="test",
    )
    await store.append_event(event)

    # ------------------------------------------------------------------
    #  Create claims with known properties
    # ------------------------------------------------------------------

    claims_data = [
        # --- Temporal references ---
        dict(text="The AI Act was published on 2024-01-15.", source_id=src_id, source_url=src_url, claim_type="fact"),
        dict(text="In March 2025, the EU commission reviewed the policy.", source_id=src_id, source_url=src_url, claim_type="fact"),
        dict(text="Q3 2024 saw major regulatory developments.", source_id=src_id, source_url=src_url, claim_type="fact"),
        dict(text="The report was published last week.", source_id=src_id, source_url=src_url, claim_type="fact"),

        # --- Uncertainty signals ---
        dict(text="The policy may reduce emissions by 20%.", source_id=src_id, source_url=src_url, claim_type="fact"),
        dict(text="According to sources, the deal is done.", source_id=src_id, source_url=src_url, claim_type="fact"),
        dict(text="The sky is blue.", source_id=src_id, source_url=src_url, claim_type="fact"),  # no uncertainty

        # --- Numeric contradictions (pair A) ---
        dict(text="The budget allocation is $4.5 million for AI safety.", source_id=src_id, source_url=src_url, claim_type="fact"),
        dict(text="The budget allocation is $12.8 million for AI safety.", source_id=uuid4(), source_url="https://source-a.helioryn.local/001", claim_type="fact"),

        # --- Temporal contradictions (pair B) ---
        dict(text="The commission hearing was in January 2024.", source_id=src_id, source_url=src_url, claim_type="fact"),
        dict(text="The commission hearing was in February 2024.", source_id=uuid4(), source_url="https://source-b.helioryn.local/001", claim_type="fact"),

        # --- Role contradictions (pair C) ---
        dict(text="John serves as President of the commission.", source_id=src_id, source_url=src_url, claim_type="fact"),
        dict(text="John serves as Vice President of the commission.", source_id=uuid4(), source_url="https://source-c.helioryn.local/001", claim_type="fact"),

        # --- Stance contradictions (pair D) ---
        dict(text="We support the new AI regulations.", source_id=src_id, source_url=src_url, claim_type="fact"),
        dict(text="We oppose the new AI regulations.", source_id=uuid4(), source_url="https://source-d.helioryn.local/001", claim_type="fact"),

        # --- Confidence: high confidence ---
        dict(text="Approximately 14.2 million records were affected.", source_id=src_id, source_url=src_url, claim_type="fact"),

        # --- Confidence: uncertain claim ---
        dict(text="The data suggests approximately 4 million may be affected.", source_id=src_id, source_url=src_url, claim_type="fact"),
    ]

    claim_ids: list[UUID] = []

    # Create entities for all the claims to link to
    entity_ids: dict[str, UUID] = {}
    for ename in ["AI Safety", "Commission", "AI Regulations"]:
        eid = await store.upsert_claim_entity(ename, "concept")
        entity_ids[ename] = eid

    for cd in claims_data:
        cid = uuid4()
        claim = Claim(
            claim_id=cid,
            source_id=cd["source_id"],
            source_url=cd["source_url"],
            extracted_at=datetime.now(timezone.utc),
            canonical_text=cd["text"],
            original_text=cd["text"],
            extraction_confidence=0.95,
            entities=[],  # filled below
            claim_type=cd["claim_type"],
            context_sentence=None,
        )
        result = await store.insert_claim(claim)
        if result:
            claim_ids.append(cid)

        # Link entities
        for ename, eid in entity_ids.items():
            if ename.lower().replace(" ", "") in cd["text"].lower().replace(" ", ""):
                await store.link_entity_to_claim(cid, eid)

        # Run enrichment
        temporal_refs = extract_temporal_references(cd["text"])
        uncertainty = detect_uncertainty(cd["text"])
        await store.enrich_claim(
            cid, temporal_refs,
            uncertainty_score=uncertainty["score"],
            uncertainty_signals=uncertainty["signals"],
        )

    print(f"\nInserted {len(claim_ids)} claims, {len(entity_ids)} entities.")

    # ------------------------------------------------------------------
    #  Verification 1: Temporal extraction
    # ------------------------------------------------------------------
    print("\n=== Temporal Extraction ===")

    t1 = await store.get_claim(claim_ids[0])
    refs = t1.get("temporal_references") or []
    if isinstance(refs, str):
        refs = json.loads(refs)
    check("ISO date extracted", any(r.get("normalized") == "2024-01-15" for r in refs),
          f"got {refs}")

    t2 = await store.get_claim(claim_ids[1])
    refs = t2.get("temporal_references") or []
    if isinstance(refs, str):
        refs = json.loads(refs)
    check("Month name + year extracted", any(r.get("normalized") == "2025-03" for r in refs),
          f"got {refs}")

    t3 = await store.get_claim(claim_ids[2])
    refs = t3.get("temporal_references") or []
    if isinstance(refs, str):
        refs = json.loads(refs)
    check("Quarter extracted", any(r.get("type") == "quarter" for r in refs),
          f"got {refs}")

    t5 = await store.get_claim(claim_ids[3])
    refs = t5.get("temporal_references") or []
    if isinstance(refs, str):
        refs = json.loads(refs)
    check("Relative date extracted", any(r.get("type") == "relative" for r in refs),
          f"got {refs}")

    # ------------------------------------------------------------------
    #  Verification 2: Uncertainty detection
    # ------------------------------------------------------------------
    print("\n=== Uncertainty Detection ===")

    def _score(c: dict | None) -> float:
        return float(c.get("uncertainty_score") or 0) if c else 0

    u1 = await store.get_claim(claim_ids[4])
    check("Modal 'may' detected", _score(u1) > 0,
          f"score={_score(u1)}")

    u2 = await store.get_claim(claim_ids[5])
    check("Attribution 'according to' detected", _score(u2) > 0,
          f"score={_score(u2)}")

    u3 = await store.get_claim(claim_ids[6])
    check("No uncertainty on factual claim", _score(u3) <= 0.001,
          f"score={_score(u3)}")

    u4 = await store.get_claim(claim_ids[15])
    check("High uncertainty from multiple signals", _score(u4) > 0.5,
          f"score={_score(u4)}")

    u5 = await store.get_claim(claim_ids[14])
    check("Quantifier alone gives partial uncertainty",
          0 < _score(u5) < 0.5,
          f"score={_score(u5)}")

    # ------------------------------------------------------------------
    #  Verification 3: Embeddings + Contradiction detection
    # ------------------------------------------------------------------
    print("\n=== Embeddings ===")

    from helioryn.embed import generate_batch_embeddings, _get_model
    _get_model()  # warm up model
    texts = []
    cid_map = []
    for cid in claim_ids:
        claim = await store.get_claim(cid)
        if claim:
            texts.append(claim["canonical_text"])
            cid_map.append(cid)

    unembedded = await store.get_claims_without_embeddings()
    if unembedded:
        batch_texts = [c["canonical_text"] for c in unembedded]
        embs = generate_batch_embeddings(batch_texts)
        batch = [(c["claim_id"], emb, "all-MiniLM-L6-v2") for c, emb in zip(unembedded, embs)]
        await store.store_embeddings_batch(batch)
        emb_count = len(batch)
    else:
        emb_count = await store.get_embedding_count()
    check("Embeddings generated", emb_count > 0,
          f"count={emb_count}")

    print("\n=== Contradiction Detection ===")

    contradicted = await store.detect_contradictions(sim_max=0.98, sim_threshold=0.40)
    check("Contradictions detected", contradicted > 0,
          f"found {contradicted} contradictions")

    if contradicted > 0:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT evidence FROM claim_relationship WHERE relationship_type = 'contradicts'"
            )
        evidence_types = set()
        for r in rows:
            etype = r["evidence"].split(":")[0] if r["evidence"] else ""
            evidence_types.add(etype)

        check("Numeric contradictions found", "numeric" in evidence_types,
              f"evidence types: {evidence_types}")
        check("Temporal contradictions found", "date conflict" in evidence_types or "month conflict" in evidence_types,
              f"evidence types: {evidence_types}")
        check("Role contradictions found", "role conflict" in evidence_types,
              f"evidence types: {evidence_types}")
        check("Stance contradictions found", "stance conflict" in evidence_types,
              f"evidence types: {evidence_types}")

    # ------------------------------------------------------------------
    #  Verification 4: Confidence factors
    # ------------------------------------------------------------------
    print("\n=== Confidence Factors ===")

    for idx in [0, 12, 13]:
        cid = claim_ids[idx]
        factors = await store.get_confidence_factors("claim", cid)
        check(f"Claim {idx} has confidence factors", len(factors) >= 1,
              f"got {len(factors)} factors")

        composite = await store.compute_claim_confidence(cid)
        check(f"Claim {idx} composite confidence in range", 0 <= composite <= 1,
              f"composite={composite}")

    # ------------------------------------------------------------------
    #  Summary
    # ------------------------------------------------------------------
    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    if FAIL:
        print("SOME CHECKS FAILED")
    else:
        print("ALL CHECKS PASSED")

    await store.close()
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(verify())
    sys.exit(0 if success else 1)

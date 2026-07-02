# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import json

from helioryn.store import EventStore


async def load_seed_entities(store: EventStore, seed_file: str, entity_type: str = "government"):
    with open(seed_file) as f:
        data = json.load(f)

    count = 0
    for entity in data:
        await store.upsert_entity(
            name=entity["name"],
            level=entity.get("level"),
            search_name=entity["search_name"],
            entity_type=entity.get("entity_type", entity_type),
            country=entity.get("country"),
            region=entity.get("region"),
            discovered_by=entity.get("discovered_by", "seed"),
            aliases=entity.get("aliases", []),
        )
        count += 1
    return count


async def auto_generate_queries_from_claim_entities(store: EventStore) -> int:
    """Create search queries from NLP-extracted claim entities.

    Runs after each discovery cycle. For each claim entity that doesn't
    already have a matching query, creates a query using the entity name
    combined with a topic-appropriate term based on the entity's dominant claim topic.
    """
    TOPIC_TERMS = {
        "ai": ("AI", "AI Research"),
        "regulation": ("regulation", "RegulationPolicy"),
        "cybersecurity": ("cybersecurity", "CyberThreats"),
        "infrastructure": ("infrastructure", "CriticalInfra"),
        "geopolitical": ("policy", "Geopolitics"),
        "science": ("research", "ScienceDiscovery"),
        "healthcare": ("health", "PublicHealth"),
        "environment": ("environment", "ClimateEnvironment"),
        "space": ("space", "SpaceExploration"),
        "arctic": ("arctic", "ArcticShipping"),
        "tribal": ("tribal", "TribalGovernance"),
        "heritage": ("heritage", "HeritagePreservation"),
    }

    async with store._pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.entity_id, e.name, c.topic,
                   COUNT(DISTINCT ce.claim_id) AS claim_count
            FROM entity e
            JOIN claim_entity ce ON ce.entity_id = e.entity_id
            JOIN claim c ON c.claim_id = ce.claim_id
            WHERE c.topic IS NOT NULL
            GROUP BY e.entity_id, e.name, c.topic
            ORDER BY claim_count DESC
            LIMIT 500
        """)

    existing = await store.list_queries(limit=10000)
    existing_texts = {q["text"].lower() for q in existing}

    seen = set()
    count = 0
    for r in rows:
        eid = r["entity_id"]
        if eid in seen:
            continue
        seen.add(eid)
        name = r["name"].strip()
        if len(name) < 3:
            continue
        topic = r["topic"]
        if topic not in TOPIC_TERMS:
            continue
        term, category = TOPIC_TERMS[topic]
        text = f"{name} {term}"
        if text.lower() in existing_texts:
            continue
        await store.upsert_query(
            text=text,
            source="claim_entity",
            parent=name,
            priority=40,
            interval_m=720,
            category=category,
        )
        count += 1
    return count


async def generate_queries_from_entities(store: EventStore):
    entities = await store.list_entities(limit=10000)

    CATEGORY_RULES = [
        (("country", "international"), "Geopolitics", ["{name}", "{name} policy"]),
        (("company",), "Models", ["{name}", "{name} AI"]),
        (("investor",), "Funding", ["{name}", "{name} funding"]),
        (("person",), "ScienceDiscovery", ["{name}", "{name} research"]),
        (("state", "agency"), "RegulationPolicy", ["{name}", "{name} regulation"]),
        (("city",), "CriticalInfra", ["{name}", "{name} infrastructure"]),
    ]

    def _match_category(etype, level):
        for levels, cat, _ in CATEGORY_RULES:
            if etype in levels or level in levels:
                return cat
        return "Models"

    def _match_terms(etype, level):
        for levels, _, terms in CATEGORY_RULES:
            if etype in levels or level in levels:
                return terms
        return ["{name}", "{name} AI"]

    count = 0
    for e in entities:
        name = e.get("search_name", e.get("name", ""))
        if not name:
            continue
        etype = e.get("entity_type", "government")
        level = e.get("level", "")
        category = _match_category(etype, level)
        terms = _match_terms(etype, level)
        for tpl in terms:
            text = tpl.format(name=name)
            await store.upsert_query(
                text=text,
                source="entity",
                parent=name,
                priority=_priority_for_level(level, etype),
                interval_m=_interval_for_level(level, etype),
                category=category,
            )
            count += 1
    return count


def _priority_for_level(level: str, entity_type: str = "government") -> int:
    if entity_type == "company":
        return 15
    if entity_type == "investor":
        return 15
    if entity_type == "person":
        return 25
    return {"country": 10, "international": 10, "agency": 20,
            "state": 20, "city": 30}.get(level, 50)


def _interval_for_level(level: str, entity_type: str = "government") -> int:
    if entity_type == "company":
        return 720
    if entity_type == "investor":
        return 1440
    if entity_type == "person":
        return 1440
    return {"country": 360, "international": 360, "agency": 720,
            "state": 720, "city": 1440}.get(level, 1440)

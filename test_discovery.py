# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from uuid import uuid4

import pytest

from helioryn.discovery.entity_db import generate_queries_from_entities, _priority_for_level, _interval_for_level
from helioryn.discovery.seed_data import _default_govs


def test_priority_by_level():
    assert _priority_for_level("country") == 10
    assert _priority_for_level("city") == 30
    assert _priority_for_level("unknown") == 50


def test_interval_by_level():
    assert _interval_for_level("country") == 360
    assert _interval_for_level("city") == 1440


def test_default_govs_has_countries():
    govs = _default_govs()
    countries = [g for g in govs if g["level"] == "country"]
    assert len(countries) >= 10
    assert "United States" in [g["name"] for g in countries]


def test_default_govs_has_all_levels():
    govs = _default_govs()
    levels = {g["level"] for g in govs}
    assert "country" in levels
    assert "international" in levels
    assert "state" in levels
    assert "city" in levels
    assert "agency" in levels


def test_curated_queries_parse_and_no_overlap():
    """Load curated queries from config, verify all parse, check no two have >0.9 similarity."""
    from pathlib import Path
    from helioryn.config import AppConfig

    config_path = str(Path(__file__).parent.parent / "helioryn.toml")
    cfg = AppConfig.load(config_path)
    queries = cfg.ingest.topics

    assert len(queries) > 0, "No curated queries loaded from config"
    assert all(q.query for q in queries), "All queries must have non-empty text"
    assert all(q.category for q in queries), "All queries must have a category/track"

    # Verify tracks are known
    known_tracks = {"Models", "Regulation", "Safety", "Open Source", "Research", "Compute", "Funding"}
    cats = {q.category for q in queries}
    unknown = cats - known_tracks
    assert not unknown, f"Unknown tracks: {unknown}"

    # Check no two queries have >0.9 word-overlap similarity
    texts = [q.query.lower() for q in queries]
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            words_i = set(texts[i].split())
            words_j = set(texts[j].split())
            overlap = len(words_i & words_j) / max(len(words_i | words_j), 1)
            assert overlap <= 0.9, (
                f"Queries too similar ({overlap:.0%}): "
                f"\"{queries[i].query}\" <-> \"{queries[j].query}\""
            )

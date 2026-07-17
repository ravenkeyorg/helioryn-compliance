# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from datetime import datetime
from uuid import UUID

from helioryn.models import (
    SourceRecord,
    SourceEvent,
    SourceSnapshot,
    SearchResult,
    FetchedContent,
    NormalizedContent,
)


def test_source_event_defaults():
    event = SourceEvent(
        source_id=UUID(int=0),
        source_url="https://example.com",
        retrieved_at=datetime(2026, 1, 1),
        raw_text="test",
        content_hash="abc",
        retrieval_method="cli",
    )
    assert event.event_id is not None
    assert event.metadata == {}


def test_search_result():
    r = SearchResult(url="https://x.com", title="X", snippet="desc", source="searxng")
    assert r.url == "https://x.com"


def test_fetched_content_defaults():
    c = FetchedContent(url="https://x.com", status_code=200, raw_html="<html/>")
    assert c.fetch_timestamp is not None


def test_normalized_content():
    n = NormalizedContent(url="https://x.com", body_text="hello")
    assert n.body_text == "hello"
    assert n.title is None

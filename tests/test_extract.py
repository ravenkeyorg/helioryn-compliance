# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from uuid import UUID

from helioryn.extract import extract_claims, extract_entities, split_sentences
from helioryn.models import SourceSnapshot


def test_split_sentences():
    text = "The Arctic is warming. Permafrost is thawing. This causes infrastructure damage."
    sents = split_sentences(text)
    assert len(sents) == 3
    assert "The Arctic is warming" in sents[0]


def test_split_sentences_skips_short():
    text = "Hi. The Arctic is warming rapidly. OK."
    sents = split_sentences(text)
    assert len(sents) == 1
    assert "Arctic" in sents[0]


def test_extract_claims_from_source():
    source = SourceSnapshot(
        source_id=UUID(int=0),
        source_url="https://example.com",
        retrieved_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        raw_text="The Arctic region is warming at four times the global average. "
                 "This permafrost thaw damages roads and buildings.",
        content_hash="abc",
        metadata={},
        retrieval_method="test",
        first_seen_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        last_updated_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    claims = extract_claims(source)
    assert len(claims) >= 2
    assert claims[0].source_id == UUID(int=0)
    assert claims[0].canonical_text != ""


def test_extract_entities():
    text = "The Arctic Council met in Norway to discuss Permafrost thaw."
    entities = extract_entities(text)
    names = [e["name"] for e in entities]
    assert "Arctic Council" in names

# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from helioryn.hasher import content_hash, url_hash


def test_content_hash_is_deterministic():
    a = content_hash("hello world")
    b = content_hash("hello world")
    assert a == b


def test_content_hash_differs_for_different_content():
    a = content_hash("hello world")
    b = content_hash("hello world!")
    assert a != b


def test_url_hash():
    h = url_hash("https://example.com")
    assert isinstance(h, str)
    assert len(h) == 64

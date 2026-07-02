# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
import pytest

from helioryn.ingest.fetcher.http import HttpFetcher
from helioryn.ingest.normalizer.readability import ReadabilityNormalizer
from helioryn.models import FetchedContent


@pytest.mark.asyncio
async def test_http_fetcher_rejects_bad_url():
    fetcher = HttpFetcher(timeout=5.0)
    with pytest.raises(Exception):
        await fetcher.fetch("https://thissitedoesnotexist99999.com/")


@pytest.mark.asyncio
async def test_readability_normalizer_works():
    html = """
    <html><head><title>Test</title></head>
    <body><article><h1>Hello</h1><p>This is a test article.</p></article></body>
    </html>
    """
    content = FetchedContent(
        url="https://example.com",
        status_code=200,
        raw_html=html,
        headers={"content-type": "text/html"},
    )
    normalizer = ReadabilityNormalizer()
    result = await normalizer.normalize(content)
    assert "test article" in result.body_text.lower()
    assert result.url == "https://example.com"

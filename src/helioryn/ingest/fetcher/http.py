# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from helioryn.ingest.base import BaseFetcher
from helioryn.models import FetchedContent


class HttpFetcher(BaseFetcher):
    def __init__(self, timeout: float = 30.0, user_agent: str | None = None):
        self.timeout = timeout
        self.user_agent = user_agent or "Helioryn/0.1"

    async def fetch(self, url: str) -> FetchedContent:
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                raise ValueError(f"Unsupported content type: {content_type}")

            return FetchedContent(
                url=str(resp.url),
                status_code=resp.status_code,
                headers=dict(resp.headers),
                raw_html=resp.text,
                fetch_timestamp=datetime.now(timezone.utc),
            )

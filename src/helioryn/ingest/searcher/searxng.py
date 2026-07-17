# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import httpx

from helioryn.ingest.base import BaseSearcher
from helioryn.models import SearchResult


class SearxngSearcher(BaseSearcher):
    EXCLUDED_DOMAINS: set[str] = {
        "msn.com", "www.msn.com",
    }

    def __init__(self, base_url: str = "http://localhost:8888", timeout: float = 15.0,
                 categories: str = "general,news,science"):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.categories = categories

    @staticmethod
    def _domain_from_url(url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).hostname or ""

    @staticmethod
    def _is_excluded(url: str, excluded: set[str]) -> bool:
        domain = SearxngSearcher._domain_from_url(url)
        for excl in excluded:
            if excl in domain or domain.endswith("." + excl):
                return True
        return False

    async def search(self, query: str, limit: int = 20, excluded_domains: set[str] | None = None,
                     pages: int = 1) -> list[SearchResult]:
        excluded = excluded_domains or self.EXCLUDED_DOMAINS
        all_results: list[SearchResult] = []
        for pageno in range(1, pages + 1):
            if len(all_results) >= limit:
                break
            params = {
                "q": query,
                "format": "json",
                "language": "en",
                "categories": self.categories,
                "pageno": pageno,
            }
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.base_url}/search", params=params)
                resp.raise_for_status()
                data = resp.json()

            for item in data.get("results", []):
                if len(all_results) >= limit:
                    break
                url = item["url"]
                if self._is_excluded(url, excluded):
                    continue
                all_results.append(
                    SearchResult(
                        url=url,
                        title=item.get("title", ""),
                        snippet=item.get("content", ""),
                        source="searxng",
                    )
                )
        return all_results

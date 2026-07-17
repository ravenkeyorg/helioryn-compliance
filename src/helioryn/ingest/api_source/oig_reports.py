# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
"""DOJ OIG grant audit report ingestor.

Periodically downloads and imports OIG audit reports relevant to OVC/VOCA grants.
Report URLs are configured in helioryn.toml [[ingest.api_sources]].

Usage:
    helioryn api-ingest   # runs all configured sources including this one
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class OigReportsSource(BaseApiSource):
    """Ingest DOJ OIG grant audit reports relevant to OVC/VOCA compliance."""

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        self.report_urls = config.get("report_urls", [])
        self.download_dir = Path(config.get("download_dir", "demo-data/oig-reports"))

    async def fetch_items(self) -> list[dict[str, Any]]:
        """Check configured report URLs and return those not yet imported."""
        results = []
        self.download_dir.mkdir(parents=True, exist_ok=True)

        for entry in self.report_urls:
            if isinstance(entry, dict):
                url = entry.get("url", "")
                title = entry.get("title", "")
            else:
                url = entry
                title = url.rstrip("/").split("/")[-1]

            if not url:
                continue

            row = await self.store.fetchrow(
                "SELECT 1 FROM source_snapshot WHERE source_url = $1 LIMIT 1",
                url,
            )
            if row:
                continue

            local_path = self.download_dir / f"{url.rstrip('/').split('/')[-1].replace('.pdf', '')}.pdf"

            if not local_path.exists():
                try:
                    resp = await self.client.get(url)
                    resp.raise_for_status()
                    local_path.write_bytes(resp.content)
                except Exception as e:
                    continue

            results.append({
                "url": url,
                "title": title,
                "path": str(local_path),
            })

        return results

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        """Convert a downloaded OIG PDF into NormalizedContent."""
        try:
            from pypdf import PdfReader
        except ImportError:
            return None

        try:
            reader = PdfReader(item["path"])
            text_parts = [p.extract_text() or "" for p in reader.pages]
            full_text = "\n".join(text_parts).strip()
            if len(full_text) < 100:
                return None

            return NormalizedContent(
                url=item["url"],
                title=item.get("title", "OIG Audit Report")[:500],
                body_text=full_text[:20000],
                publish_date=None,
                metadata={
                    "source": "oig",
                    "report_url": item["url"],
                    "oig_report_title": item.get("title", ""),
                    "topic": self.topic or "ovc",
                },
            )
        except Exception:
            return None

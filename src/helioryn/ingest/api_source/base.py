# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
import asyncio
from abc import ABC, abstractmethod
from typing import Any
from helioryn.config import AppConfig
from helioryn.models import NormalizedContent


class BaseApiSource(ABC):
    """Abstract base for API-based data sources (grants.gov, Federal Register, etc.)."""

    def __init__(self, config: dict, ingestor, store):
        self.config = config
        self.ingestor = ingestor
        self.store = store
        self.name = config.get("name", "api-source")
        self.interval_minutes = config.get("interval_minutes", 120)
        self.base_url = config.get("base_url", "")
        self.params = config.get("params", {})
        self.headers = config.get("headers", {})
        self.topic = config.get("topic", "")
        self.credential_service = config.get("credential_service", "")

    async def resolve_api_key(self) -> str:
        """Look up API key from credential store, env var, or config."""
        key = ""
        if self.credential_service and self.store:
            try:
                cred = await self.store.get_credential_by_service(self.credential_service)
                if cred:
                    key = cred.get("api_key", "")
                    if not self.base_url and cred.get("base_url"):
                        self.base_url = cred["base_url"]
            except Exception:
                pass
        if not key:
            key = self.config.get("api_key", "")
        if not key:
            env_name = self.credential_service.upper().replace("-", "_") + "_API_KEY"
            import os
            key = os.environ.get(env_name, "")
        return key

    @abstractmethod
    async def fetch_items(self) -> list[dict[str, Any]]:
        """Fetch items from the API. Returns list of raw item dicts."""
        ...

    @abstractmethod
    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        """Transform an API item into NormalizedContent for ingestion."""
        ...

    async def ingest_items(self) -> int:
        """Fetch and ingest all items from the API source."""
        items = await self.fetch_items()
        count = 0
        for item in items:
            try:
                normalized = self.item_to_normalized(item)
                if normalized is None:
                    continue
                result = await self.ingestor.ingest(normalized)
                if result is not None:
                    count += 1
            except Exception as e:
                print(f"  Error ingesting API item: {e}")
        return count

    async def run_cycle(self) -> dict:
        """Run one full cycle: fetch, transform, ingest."""
        items = await self.fetch_items()
        ingested = 0
        skipped = 0
        errors = 0
        for item in items:
            try:
                normalized = self.item_to_normalized(item)
                if normalized is None:
                    skipped += 1
                    continue
                result = await self.ingestor.ingest(normalized)
                if result is not None:
                    ingested += 1
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                print(f"  Error: {e}")
        return {"source": self.name, "fetched": len(items), "ingested": ingested, "skipped": skipped, "errors": errors}

# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

from helioryn.hasher import content_hash
from helioryn.ingest.base import BaseIngestor
from helioryn.models import NormalizedContent, SourceEvent
from helioryn.store import EventStore


class PostgresIngestor(BaseIngestor):
    def __init__(self, store: EventStore, domain_denylist: list[str] | None = None):
        self._store = store
        self._denylist = [d.lower().removeprefix("www.") for d in (domain_denylist or [])]

    async def ingest(self, normalized: NormalizedContent) -> SourceEvent | None:
        domain = urlparse(normalized.url).hostname or ""
        domain = domain.lower().removeprefix("www.")
        if any(domain == d or domain.endswith("." + d) for d in self._denylist):
            return None
        h = content_hash(normalized.body_text)

        existing_source = await self._store.is_content_known(h)
        source_id = existing_source if existing_source else uuid4()

        event = SourceEvent(
            source_id=source_id,
            source_url=normalized.url,
            title=normalized.title,
            author=normalized.author,
            publish_date=normalized.publish_date,
            retrieved_at=datetime.now(timezone.utc),
            raw_text=normalized.body_text,
            raw_html=normalized.raw_html,
            content_hash=h,
            metadata=normalized.metadata,
            retrieval_method="http_fetch",
        )

        stored = await self._store.append_event(event)
        return stored

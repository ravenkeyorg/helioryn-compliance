# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

from abc import ABC, abstractmethod

from helioryn.models import (
    FetchedContent,
    NormalizedContent,
    SearchResult,
    SourceEvent,
)


class BaseSearcher(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int = 20) -> list[SearchResult]: ...


class BaseFetcher(ABC):
    @abstractmethod
    async def fetch(self, url: str) -> FetchedContent: ...


class BaseNormalizer(ABC):
    @abstractmethod
    async def normalize(self, content: FetchedContent) -> NormalizedContent: ...


class BaseIngestor(ABC):
    @abstractmethod
    async def ingest(self, normalized: NormalizedContent) -> SourceEvent | None: ...

# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

from helioryn.ingest.base import BaseFetcher, BaseIngestor, BaseNormalizer, BaseSearcher
from helioryn.ingest.searcher.searxng import SearxngSearcher
from helioryn.ingest.fetcher.http import HttpFetcher
from helioryn.ingest.normalizer.readability import ReadabilityNormalizer
from helioryn.ingest.ingestor.postgres import PostgresIngestor
from helioryn.config import AppConfig
from helioryn.store import EventStore


def create_searcher(config: AppConfig) -> BaseSearcher:
    t = config.ingest.searcher.type
    if t == "searxng":
        return SearxngSearcher(
            base_url=config.ingest.searcher.base_url,
            timeout=config.ingest.searcher.timeout,
            categories=config.ingest.searcher.categories,
        )
    raise ValueError(f"Unknown searcher type: {t}")


def create_fetcher(config: AppConfig) -> BaseFetcher:
    t = config.ingest.fetcher.type
    if t == "http":
        return HttpFetcher(
            timeout=config.ingest.fetcher.timeout,
            user_agent=config.ingest.fetcher.user_agent,
        )
    raise ValueError(f"Unknown fetcher type: {t}")


def create_normalizer(config: AppConfig) -> BaseNormalizer:
    t = config.ingest.normalizer.type
    if t == "readability":
        return ReadabilityNormalizer()
    raise ValueError(f"Unknown normalizer type: {t}")


def create_ingestor(config: AppConfig, store: EventStore) -> BaseIngestor:
    t = config.ingest.ingestor.type
    if t == "postgres":
        return PostgresIngestor(store, config.ingest.domain_denylist)
    raise ValueError(f"Unknown ingestor type: {t}")

# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import tomli


@dataclass
class SearcherConfig:
    type: str = "searxng"
    base_url: str = "http://localhost:8888"
    categories: str = "general,news"
    language: str = "en"
    timeout: float = 15.0


@dataclass
class FetcherConfig:
    type: str = "http"
    timeout: float = 30.0
    user_agent: str = "Helioryn/0.1"


@dataclass
class NormalizerConfig:
    type: str = "readability"


@dataclass
class IngestorConfig:
    type: str = "postgres"


@dataclass
class TopicConfig:
    query: str = ""
    interval_minutes: int = 360
    language: str = "en"
    category: str = ""


@dataclass
class IngestConfig:
    searcher: SearcherConfig = field(default_factory=SearcherConfig)
    fetcher: FetcherConfig = field(default_factory=FetcherConfig)
    normalizer: NormalizerConfig = field(default_factory=NormalizerConfig)
    ingestor: IngestorConfig = field(default_factory=IngestorConfig)
    topics: list[TopicConfig] = field(default_factory=list)
    fetch_delay: float = 5.0
    domain_denylist: list[str] = field(default_factory=list)
    auto_generate_queries: bool = True


@dataclass
class AppConfig:
    database_url: str = ""
    ingest: IngestConfig = field(default_factory=IngestConfig)

    @classmethod
    def load(cls, path: str | None = None) -> AppConfig:
        cfg = cls()

        if path and os.path.exists(path):
            with open(path, "rb") as f:
                data = tomli.load(f)

            db = data.get("database", {})
            cfg.database_url = db.get("url", "")

            ingest_data = data.get("ingest", {})
            ing_cfg = cfg.ingest

            searcher_data = ingest_data.get("searcher", {})
            ing_cfg.searcher = SearcherConfig(
                type=searcher_data.get("type", "searxng"),
                base_url=searcher_data.get("base_url", "http://localhost:8888"),
                categories=searcher_data.get("categories", "general,news"),
                language=searcher_data.get("language", "en"),
                timeout=searcher_data.get("timeout", 15.0),
            )

            fetcher_data = ingest_data.get("fetcher", {})
            ing_cfg.fetcher = FetcherConfig(
                type=fetcher_data.get("type", "http"),
                timeout=fetcher_data.get("timeout", 30.0),
                user_agent=fetcher_data.get("user_agent", "Helioryn/0.1"),
            )

            normalizer_data = ingest_data.get("normalizer", {})
            ing_cfg.normalizer = NormalizerConfig(
                type=normalizer_data.get("type", "readability"),
            )

            ingestor_data = ingest_data.get("ingestor", {})
            ing_cfg.ingestor = IngestorConfig(
                type=ingestor_data.get("type", "postgres"),
            )

    ing_cfg.fetch_delay = ingest_data.get("fetch_delay", 5.0)
    ing_cfg.user_agent = ingest_data.get("user_agent", "Helioryn/0.1")
    ing_cfg.domain_denylist = ingest_data.get("domain_denylist", [])
    ing_cfg.auto_generate_queries = ingest_data.get("auto_generate_queries", True)

            for topic in ingest_data.get("topics", {}).get("items", []):
                ing_cfg.topics.append(
                    TopicConfig(
                        query=topic.get("query", ""),
                        interval_minutes=topic.get("interval_minutes", 360),
                        language=topic.get("language", "en"),
                        category=topic.get("category", ""),
                    )
                )

        if not cfg.database_url:
            user = getpass.getuser()
            socket = "/tmp" if sys.platform == "darwin" else "/var/run/postgresql"
            cfg.database_url = os.getenv(
                "HELIORYN_DATABASE_URL",
                f"postgresql://{user}@/helioryn_dev?host={socket}",
            )

        return cfg

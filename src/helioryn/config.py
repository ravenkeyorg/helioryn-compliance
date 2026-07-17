# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
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
    api_sources: list[dict] = field(default_factory=list)
    fetch_delay: float = 5.0
    domain_denylist: list[str] = field(default_factory=list)


@dataclass
class OllamaConfig:
    model: str = "qwen2.5:14b"
    base_url: str = "http://localhost:11434"
    max_tokens: int = 4096
    temperature: float = 0.1


@dataclass
class OpenCodeConfig:
    base_url: str = "https://api.opencode.ai/v1"
    api_key: str = ""


@dataclass
class LLMConfig:
    provider: str = "ollama"  # "ollama" | "opencode-go"
    model: str = "qwen2.5:14b"
    max_tokens: int = 4096
    temperature: float = 0.1


@dataclass
class AuthConfig:
    api_key: str = ""
    session_secret: str = "helioryn-dev-secret"
    admin_password: str = ""


@dataclass
class AppConfig:
    database_url: str = ""
    redis_url: str = ""
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    opencode: OpenCodeConfig = field(default_factory=OpenCodeConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)

    @classmethod
    def _find_config(cls, path: str | None = None) -> str | None:
        if path and os.path.exists(path):
            return path
        candidates = [
            Path.cwd() / "helioryn.toml",
            Path(__file__).resolve().parent.parent.parent / "helioryn.toml",
            Path.home() / ".helioryn" / "helioryn.toml",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return None

    @classmethod
    def load(cls, path: str | None = None) -> AppConfig:
        cfg = cls()
        found = cls._find_config(path)

        if found:
            with open(found, "rb") as f:
                data = tomli.load(f)

            db = data.get("database", {})
            cfg.database_url = db.get("url", "")

            cache_data = data.get("cache", {})
            cfg.redis_url = cache_data.get("url", "")

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
            ing_cfg.api_sources = ingest_data.get("api_sources", [])

            for topic in ingest_data.get("topics", {}).get("items", []):
                ing_cfg.topics.append(
                    TopicConfig(
                        query=topic.get("query", ""),
                        interval_minutes=topic.get("interval_minutes", 360),
                        language=topic.get("language", "en"),
                        category=topic.get("category", ""),
                    )
                )

            ollama_data = data.get("ollama", {})
            cfg.ollama = OllamaConfig(
                model=ollama_data.get("model", "qwen2.5:7b"),
                base_url=ollama_data.get("base_url", "http://localhost:11434"),
                max_tokens=ollama_data.get("max_tokens", 4096),
                temperature=ollama_data.get("temperature", 0.1),
            )

            llm_data = data.get("llm", {})
            cfg.llm = LLMConfig(
                provider=llm_data.get("provider", "opencode-go"),
                model=llm_data.get("model", "opencode-go/deepseek-v4-pro"),
                max_tokens=llm_data.get("max_tokens", 4096),
                temperature=llm_data.get("temperature", 0.1),
            )

            opencode_data = data.get("opencode", {})
            cfg.opencode = OpenCodeConfig(
                base_url=opencode_data.get("base_url", "https://api.opencode.ai/v1"),
                api_key=opencode_data.get("api_key", ""),
            )

            auth_data = data.get("auth", {})
            cfg.auth.api_key = auth_data.get("api_key", "")
            cfg.auth.session_secret = auth_data.get("session_secret", "helioryn-dev-secret")
            cfg.auth.admin_password = auth_data.get("admin_password", "")

        env_key = os.environ.get("HELIORYN_API_KEY", "")
        env_admin = os.environ.get("HELIORYN_ADMIN_PASSWORD", "")
        if env_key:
            cfg.auth.api_key = env_key
        if env_admin:
            cfg.auth.admin_password = env_admin

        if not cfg.database_url:
            user = getpass.getuser()
            socket = "/tmp" if sys.platform == "darwin" else "/var/run/postgresql"
            cfg.database_url = os.getenv(
                "HELIORYN_DATABASE_URL",
                f"postgresql://{user}@/localhost_dev?host={socket}",
            )

        if not cfg.redis_url:
            cfg.redis_url = os.getenv("HELIORYN_REDIS_URL", "redis://localhost:6379/0")

        return cfg

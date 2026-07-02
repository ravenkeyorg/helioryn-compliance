# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
"""Redis cache and pub/sub for daemon coordination and web server caching."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

DEFAULT_REDIS_URL = "redis://localhost:6379/0"

CH_PIPELINE = "helioryn:pipeline"
CH_SCORER = "helioryn:scorer"
CH_ANALYZER = "helioryn:analyzer"
CH_INTERPRETER = "helioryn:interpreter"
CH_DAEMON = "helioryn:daemon"


class HeliorynCache:
    """Redis-backed cache and pub/sub.

    Each daemon subprocess and the web server connect independently.
    Redis is optional — all operations are best-effort with graceful
    degradation if Redis is unreachable.
    """

    def __init__(self, url: str | None = None):
        self.url = url or DEFAULT_REDIS_URL
        self._redis: Any = None

    async def connect(self):
        self._redis = aioredis.from_url(self.url, decode_responses=True)

    async def close(self):
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

    @property
    def r(self):
        if self._redis is None:
            raise RuntimeError("HeliorynCache not connected")
        return self._redis

    # ── Pub/sub ──

    async def publish(self, channel: str, message: dict):
        await self.r.publish(channel, json.dumps(message))

    async def subscribe(self, channel: str):
        pubsub = self.r.pubsub()
        await pubsub.subscribe(channel)
        return pubsub

    async def get_message(self, pubsub, timeout: float = 2.0) -> dict | None:
        try:
            msg = await pubsub.get_message(timeout=timeout)
            if msg and msg.get("type") == "message":
                return json.loads(msg["data"])
        except Exception:
            pass
        return None

    # ── Status cache (daemon ↔ web server) ──

    async def put_status(self, key: str, value: dict, ttl: int = 300):
        await self.r.setex(f"helioryn:status:{key}", ttl, json.dumps(value))

    async def get_status(self, key: str) -> dict | None:
        val = await self.r.get(f"helioryn:status:{key}")
        return json.loads(val) if val else None

    # ── Page cache (web server) ──

    async def get_page(self, cache_key: str) -> str | None:
        return await self.r.get(f"helioryn:page:{cache_key}")

    async def set_page(self, cache_key: str, html: str, ttl: int = 30):
        await self.r.setex(f"helioryn:page:{cache_key}", ttl, html)

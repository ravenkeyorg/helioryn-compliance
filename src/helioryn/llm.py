# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

import httpx

_AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"


class LLMProvider(Protocol):
    async def generate(
        self,
        system_prompt: str,
        context: str,
        question: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> str:
        ...


def _load_opencode_key() -> str | None:
    try:
        if _AUTH_PATH.exists():
            data = json.loads(_AUTH_PATH.read_text())
            for provider in ("opencode-go", "opencode-zen", "opencode"):
                key = data.get(provider, {}).get("key")
                if key:
                    return key
    except Exception:
        pass
    return os.environ.get("OPENCODE_API_KEY") or os.environ.get("LLM_API_KEY")


class OllamaProvider:
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url.rstrip("/")

    async def generate(
        self,
        system_prompt: str,
        context: str,
        question: str,
        *,
        model: str = "qwen2.5:7b",
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> str:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                import logging
                logging.getLogger(__name__).error(
                    "Ollama API returned %d: %s", resp.status_code, resp.text[:500]
                )
                return f"[Ollama API error: {resp.status_code}]"
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return data.get("message", {}).get("content", "") or data.get("response", "")


class OpenCodeGoProvider:
    def __init__(self, base_url: str = "https://api.opencode.ai/v1", api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or _load_opencode_key() or ""

    async def generate(
        self,
        system_prompt: str,
        context: str,
        question: str,
        *,
        model: str = "opencode-go/deepseek-v4-pro",
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": False,
                },
            )
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return data.get("error", {}).get("message", str(data))


def create_llm(config) -> LLMProvider:
    provider = config.llm.provider
    if provider == "ollama":
        return OllamaProvider(base_url=config.ollama.base_url)
    elif provider == "opencode-go":
        return OpenCodeGoProvider(
            base_url=config.opencode.base_url,
            api_key=config.opencode.api_key,
        )
    raise ValueError(f"Unknown LLM provider: {provider}")

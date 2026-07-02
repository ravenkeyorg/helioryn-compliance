# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import json
from datetime import datetime

from bs4 import BeautifulSoup
from trafilatura import extract

from helioryn.ingest.base import BaseNormalizer
from helioryn.models import FetchedContent, NormalizedContent


def _extract_head_meta(soup: BeautifulSoup) -> dict:
    meta = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name") or tag.get("property") or tag.get("charset")
        content = tag.get("content", "")
        if name and content:
            key = name.lower().replace(":", "_")
            if key not in meta:
                meta[key] = content.strip()
        if tag.get("charset"):
            meta["charset"] = tag["charset"]
        http_equiv = tag.get("http-equiv")
        if http_equiv and tag.get("content"):
            meta[f"http_{http_equiv.lower().replace('-', '_')}"] = tag["content"].strip()
    return meta


def _extract_title(soup: BeautifulSoup) -> str | None:
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    return None


def _extract_author(soup: BeautifulSoup) -> str | None:
    for attrs in [
        {"name": "author"},
        {"property": "article:author"},
        {"property": "author"},
    ]:
        tag = soup.find("meta", attrs=attrs)
        if tag:
            content = tag.get("content")
            if content:
                return content.strip()
    tag = soup.find(attrs={"rel": "author"})
    if tag:
        content = tag.get_text(strip=True)
        if content:
            return content
    for cls in ["author", "byline", "contributor"]:
        tag = soup.find(class_=cls) or soup.find(id=cls)
        if tag:
            txt = tag.get_text(strip=True)
            if txt and len(txt) < 120:
                return txt
    return None


def _extract_date(soup: BeautifulSoup) -> datetime | None:
    for attrs in [
        {"property": "article:published_time"},
        {"name": "date"},
        {"property": "publication_date"},
        {"name": "publication-date"},
    ]:
        tag = soup.find("meta", attrs=attrs)
        if tag:
            val = tag.get("content")
            if val:
                try:
                    return datetime.fromisoformat(val.strip())
                except (ValueError, TypeError):
                    pass
    for tag in soup.find_all("time"):
        val = tag.get("datetime")
        if val:
            try:
                return datetime.fromisoformat(val.strip())
            except (ValueError, TypeError):
                pass
    return None


def _extract_canonical(soup: BeautifulSoup) -> str | None:
    tag = soup.find("link", rel="canonical")
    if tag and tag.get("href"):
        return tag["href"].strip()
    return None


def _extract_language(soup: BeautifulSoup) -> str | None:
    html = soup.find("html")
    if html and html.get("lang"):
        return html["lang"].strip().lower()
    return None


def _extract_jsonld(soup: BeautifulSoup) -> list[dict] | None:
    scripts = soup.find_all("script", type="application/ld+json")
    if not scripts:
        return None
    results = []
    for s in scripts:
        if s.string:
            try:
                data = json.loads(s.string.strip())
                results.append(data if isinstance(data, list) else [data])
            except (json.JSONDecodeError, ValueError):
                pass
    return [item for sublist in results for item in sublist] if results else None


def _extract_feeds(soup: BeautifulSoup) -> list[dict]:
    feeds = []
    for link in soup.find_all("link", type=["application/rss+xml", "application/atom+xml"]):
        href = link.get("href")
        if href:
            feeds.append({
                "href": href.strip(),
                "title": link.get("title", "").strip(),
                "type": link["type"],
            })
    return feeds or None


class ReadabilityNormalizer(BaseNormalizer):
    async def normalize(self, content: FetchedContent) -> NormalizedContent:
        text = extract(
            content.raw_html,
            output_format="txt",
            include_tables=False,
            include_images=False,
            include_links=False,
            no_fallback=False,
        )

        body = (text or content.raw_html).strip()
        soup = BeautifulSoup(content.raw_html, "html.parser")
        title = _extract_title(soup)
        author = _extract_author(soup)
        publish_date = _extract_date(soup)

        meta = {"fetched_status": content.status_code}

        head_meta = _extract_head_meta(soup)
        if head_meta:
            meta["head_meta"] = head_meta

        canonical = _extract_canonical(soup)
        if canonical:
            meta["canonical_url"] = canonical

        lang = _extract_language(soup)
        if lang:
            meta["language"] = lang

        jsonld = _extract_jsonld(soup)
        if jsonld:
            meta["jsonld"] = jsonld

        feeds = _extract_feeds(soup)
        if feeds:
            meta["feeds"] = feeds

        return NormalizedContent(
            url=content.url,
            title=title,
            author=author,
            publish_date=publish_date,
            body_text=body,
            raw_html=content.raw_html,
            metadata=meta,
        )

# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import urlparse

from helioryn.config import AppConfig
from helioryn.ingest import create_searcher, create_fetcher, create_normalizer, create_ingestor
from helioryn.ingest.base import BaseSearcher, BaseFetcher, BaseNormalizer, BaseIngestor
from helioryn.store import EventStore
import helioryn.log as log


async def run_discovery_cycle(config: AppConfig, store: EventStore,
                              searcher: BaseSearcher | None = None,
                              fetcher: BaseFetcher | None = None,
                              normalizer: BaseNormalizer | None = None,
                              max_queries: int = 30,
                              results_per_query: int = 10,
                              search_pages: int = 2,
                              skip_urls: set[str] | None = None,
                              progress_callback=None):
    searcher = searcher or create_searcher(config)
    fetcher = fetcher or create_fetcher(config)
    normalizer = normalizer or create_normalizer(config)
    ingestor = create_ingestor(config, store)
    skip_urls = skip_urls or set()
    denylist = []
    for d in config.ingest.domain_denylist:
        d = d.lower().removeprefix("www.")
        denylist.append(d)

    queries = await store.get_next_queries(limit=max_queries)

    if not queries:
        if progress_callback:
            progress_callback("No queries due.")
        return 0, 0, 0

    total_ingested = 0
    total_skipped = 0
    total_errors = 0

    # Step 1: Run all SearXNG searches in parallel
    async def _search_one(q: dict) -> list | None:
        query_text = q["text"]
        try:
            results = await searcher.search(query_text, limit=results_per_query, pages=search_pages)
            return results
        except Exception as e:
            log.emit("run_started", run_id=str(q["query_id"])[:8], topic=query_text[:60], error=str(e))
            return None

    search_tasks = [_search_one(q) for q in queries]
    all_search_results = await asyncio.gather(*search_tasks)

    # Step 2: Process results from each query
    for q, results in zip(queries, all_search_results):
        if not results:
            continue
        query_text = q["text"]
        log.emit("run_started", run_id=str(q["query_id"])[:8], topic=query_text[:60],
                 results=len(results))

        for r in results:
            archived = await store.is_url_archived(r.url)
            if archived:
                total_skipped += 1
                if progress_callback:
                    progress_callback(f"  Skipped (archived): {r.title[:50]}")
                continue

            if r.url in skip_urls:
                total_skipped += 1
                continue

            domain = (urlparse(r.url).hostname or "").lower()
            if domain.startswith("www."):
                domain = domain[4:]
            if any(domain == d or domain.endswith("." + d) for d in denylist):
                total_skipped += 1
                if progress_callback:
                    progress_callback(f"  Skipped (denylisted domain): {r.title[:50]}")
                continue

            try:
                fetched = await fetcher.fetch(r.url)
                normalized = await normalizer.normalize(fetched)
                category = q.get("category", "") or ""
                normalized.metadata = normalized.metadata or {}
                normalized.metadata["query_category"] = category
                normalized.metadata["query_id"] = str(q.get("query_id", ""))
                normalized.metadata["query_text"] = q.get("text", "")
                ingested = await ingestor.ingest(normalized)
                if ingested:
                    total_ingested += 1
                    log.emit("source_ingested", url=r.url, title=r.title)
                    if progress_callback:
                        progress_callback(f"  Ingested: {r.title[:50]}")
                    if config.ingest.fetch_delay > 0:
                        await asyncio.sleep(config.ingest.fetch_delay)
                else:
                    total_skipped += 1
            except Exception as e:
                total_errors += 1
                skip_urls.add(r.url)
                log.emit("source_failed", url=r.url, error=str(e))
                if progress_callback:
                    progress_callback(f"  Failed: {r.title[:30] if r.title else r.url[:50]} — {e}")

        await store.mark_query_run(q["query_id"])

    if progress_callback:
        progress_callback(f"Done: {total_ingested} ingested, {total_skipped} skipped, {total_errors} errors")
    log.emit("run_completed", ingested=total_ingested,
             skipped=total_skipped, errors=total_errors)
    return total_ingested, total_skipped, total_errors

from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class CrimeSolutionsSource(BaseApiSource):
    """Ingest evidence-based programs from DOJ CrimeSolutions.gov. No auth required."""

    API_URL = "https://data.ojp.usdoj.gov/resource/6h3w-ci9p.json"

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.topic_filter = config.get("topic_filter", "")

    async def fetch_items(self) -> list[dict[str, Any]]:
        items = []
        offset = 0
        while True:
            try:
                params = {"$limit": 1000, "$offset": offset}
                if self.topic_filter:
                    params["$where"] = f"topic='{self.topic_filter}'"
                resp = await self.client.get(self.API_URL, params=params)
                if resp.status_code != 200:
                    break
                batch = resp.json()
                if not batch:
                    break
                items.extend(batch)
                if len(batch) < 1000:
                    break
                offset += 1000
            except Exception as e:
                print(f"  CrimeSolutions error: {e}")
                break
        print(f"  Fetched {len(items)} programs from CrimeSolutions.gov")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        title = item.get("program_name", "") or item.get("title", "") or ""
        summary = item.get("summary", "") or item.get("description", "") or ""
        topic = item.get("topic", "") or ""
        rating = item.get("rating", "") or item.get("evidence_rating", "") or ""
        categories = item.get("categories", "") or item.get("program_type", "") or ""

        if not title:
            return None

        body = f"Program: {title}\n"
        if topic:
            body += f"Topic: {topic}\n"
        if rating:
            body += f"Rating: {rating}\n"
        if categories:
            body += f"Categories: {categories}\n"
        if summary:
            body += f"\n{summary}"

        mapped_topic = self.topic or "doj-grants"
        text_upper = f"{title} {topic} {categories}".upper()
        if "VICTIM" in text_upper or "OVC" in text_upper:
            mapped_topic = "ovc"
        elif "TRIBAL" in text_upper or "INDIAN" in text_upper:
            mapped_topic = "tribal-funding"

        return NormalizedContent(
            url="https://crimesolutions.ojp.gov",
            title=title[:500] if title else "DOJ Program",
            body_text=body,
            publish_date=None,
            metadata={
                "source": "crime_solutions",
                "topic": mapped_topic,
                "rating": rating,
                "query_category": mapped_topic,
            },
        )

import os
from datetime import date, timedelta
from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class GovInfoSource(BaseApiSource):
    """Ingest US government publications from GovInfo.gov API. Requires api.data.gov key."""

    API_BASE = "https://api.govinfo.gov"
    COLLECTIONS = ["PLAW", "GAOREPORTS", "BUDGET", "CFR"]

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.days_back = config.get("days_back", 14)
        self.collections = config.get("collections", self.COLLECTIONS)

    async def resolve_api_key(self) -> str:
        key = await super().resolve_api_key()
        return key or "DEMO_KEY"

    async def fetch_items(self) -> list[dict[str, Any]]:
        self.api_key = await self.resolve_api_key()
        items = []
        start = (date.today() - timedelta(days=self.days_back)).isoformat() + "T00:00:00Z"
        end = date.today().isoformat() + "T23:59:59Z"

        for coll in self.collections:
            offset_mark = "*"
            while True:
                try:
                    url = f"{self.API_BASE}/collections/{coll}/{start}/{end}"
                    resp = await self.client.get(
                        url,
                        params={"offsetMark": offset_mark, "pageSize": 100, "api_key": self.api_key},
                    )
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    packages = data.get("packages", [])
                    if not packages:
                        break
                    for pkg in packages:
                        pkg["_collection"] = coll
                        items.append(pkg)

                    next_mark = data.get("nextPage", {}).get("offsetMark") if isinstance(data.get("nextPage"), dict) else None
                    if not next_mark or next_mark == offset_mark:
                        break
                    offset_mark = next_mark
                except Exception as e:
                    print(f"  GovInfo error for {coll}: {e}")
                    break

        print(f"  Fetched {len(items)} publications from GovInfo.gov")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        package_id = item.get("packageId", "") or item.get("id", "")
        title = item.get("title", "") or item.get("packageId", "")
        collection = item.get("_collection", "")
        publish_date = item.get("publishDate", "") or item.get("dateIssued", "") or item.get("date", "")
        summary = item.get("summary", "") or item.get("description", "") or ""

        if not package_id:
            return None

        body = f"Title: {title}\n"
        body += f"Package ID: {package_id}\n"
        body += f"Collection: {collection}\n"
        if publish_date:
            body += f"Date: {publish_date}\n"
        if summary:
            body += f"\n{summary}"

        topic = self.topic or "grant-regulations"
        if collection == "PLAW":
            topic = "grant-regulations"
        elif collection == "GAOREPORTS":
            text_upper = f"{title} {summary}".upper()
            if "TRIBAL" in text_upper or "INDIAN" in text_upper:
                topic = "tribal-funding"
            elif "GRANT" in text_upper or "COMPLIANCE" in text_upper or "AUDIT" in text_upper:
                topic = "grant-regulations"
            else:
                topic = "grant-opportunities"
        elif collection == "BUDGET":
            topic = "grant-opportunities"
        elif collection == "CFR":
            topic = "grant-regulations"

        return NormalizedContent(
            url=f"https://www.govinfo.gov/app/details/{package_id}" if package_id else "https://www.govinfo.gov",
            title=title[:500] if title else "GovInfo Document",
            body_text=body,
            publish_date=None,
            metadata={
                "source": "govinfo",
                "package_id": package_id,
                "collection": collection,
                "publish_date": publish_date,
                "topic": topic,
                "query_category": topic,
            },
        )

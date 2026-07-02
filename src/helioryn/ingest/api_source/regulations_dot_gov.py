import os
from datetime import date, timedelta
from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class RegulationsGovSource(BaseApiSource):
    """Ingest rulemaking documents from Regulations.gov API. Requires api.data.gov key."""

    API_BASE = "https://api.regulations.gov/v4"
    AGENCIES = ["DOJ", "HHS", "HUD", "EPA", "DOI", "USDA", "ED", "DOT", "SSA"]
    DOC_TYPES = ["Proposed Rule", "Rule", "Notice"]
    TOPIC_MAP = {
        "DOJ": "doj-grants",
        "HHS": "hhs-grants",
        "HUD": "hud-grants",
        "DOI": "tribal-funding",
        "EPA": "grant-regulations",
        "USDA": "grant-opportunities",
        "ED": "grant-opportunities",
        "DOT": "grant-opportunities",
    }

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.days_back = config.get("days_back", 7)
        self.agency_ids = config.get("agencies", self.AGENCIES)

    async def resolve_api_key(self) -> str:
        key = await super().resolve_api_key()
        return key or "DEMO_KEY"

    async def fetch_items(self) -> list[dict[str, Any]]:
        api_key = await self.resolve_api_key()
        items = []
        start = (date.today() - timedelta(days=self.days_back)).isoformat()

        for agency_id in self.agency_ids:
            page = 1
            while page <= 20:
                try:
                    resp = await self.client.get(
                        f"{self.API_BASE}/documents",
                        params={
                            "filter[agencyId]": agency_id,
                            "filter[postedDate][ge]": start,
                            "filter[documentType]": self.DOC_TYPES,
                            "page[size]": 250,
                            "page[number]": page,
                        },
                        headers={"X-Api-Key": api_key},
                    )
                    if resp.status_code != 200:
                        if resp.status_code == 429:
                            print(f"  Regulations.gov rate limited for {agency_id}")
                        break
                    data = resp.json()
                    docs = data.get("data", [])
                    if not docs:
                        break
                    for doc in docs:
                        attrs = doc.get("attributes", {})
                        attrs["_agency_id"] = agency_id
                        attrs["_doc_id"] = doc.get("id", "")
                        items.append(attrs)
                    meta = data.get("meta", {})
                    if not meta.get("hasNextPage"):
                        break
                    page += 1
                except Exception as e:
                    print(f"  Regulations.gov error for {agency_id}: {e}")
                    break

        print(f"  Fetched {len(items)} documents from Regulations.gov")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        title = item.get("title", "") or ""
        doc_id = item.get("_doc_id", "")
        agency_id = item.get("_agency_id", "")
        doc_type = item.get("documentType", "")
        posted = item.get("postedDate", "")
        comment_end = item.get("commentEndDate", "")
        description = item.get("description", "") or item.get("abstract", "") or ""
        docket_id = item.get("docketId", "")

        if not title:
            return None

        body = f"Title: {title}\n"
        body += f"Document ID: {doc_id}\n"
        body += f"Agency: {agency_id}\n"
        body += f"Type: {doc_type}\n"
        if posted:
            body += f"Posted: {posted}\n"
        if comment_end:
            body += f"Comment Deadline: {comment_end}\n"
        if docket_id:
            body += f"Docket: {docket_id}\n"
        if description:
            body += f"\n{description}"

        topic = self.topic or self.TOPIC_MAP.get(agency_id, "grant-regulations")
        title_upper = title.upper()
        if "VICTIM" in title_upper or "OVC" in title_upper or "VOCA" in title_upper:
            topic = "ovc"
        elif "TRIBAL" in title_upper or "INDIAN" in title_upper:
            topic = "tribal-funding"

        return NormalizedContent(
            url=f"https://www.regulations.gov/document/{doc_id}" if doc_id else "https://www.regulations.gov",
            title=title,
            body_text=body,
            publish_date=None,
            metadata={
                "source": "regulations_gov",
                "document_id": doc_id,
                "agency": agency_id,
                "document_type": doc_type,
                "posted_date": posted,
                "comment_end_date": comment_end,
                "docket_id": docket_id,
                "topic": topic,
                "query_category": topic,
            },
        )

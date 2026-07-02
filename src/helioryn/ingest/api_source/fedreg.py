# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from datetime import date, timedelta
from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class FederalRegisterSource(BaseApiSource):
    """Ingest Federal Register documents related to grants and compliance."""

    API_BASE = "https://www.federalregister.gov/api/v1"
    AGENCY_QUERIES = [
        "justice-department",
        "health-and-human-services-department",
        "housing-and-urban-development-department",
        "interior-department",
        "environmental-protection-agency",
    ]
    TOPIC_MAP = {
        "justice-department": "doj-grants",
        "health-and-human-services": "hhs-grants",
        "housing-and-urban-development": "hud-grants",
        "interior-department": "tribal-funding",
        "environmental-protection": "grant-regulations",
    }

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.days_back = config.get("days_back", 3)

    async def fetch_items(self) -> list[dict[str, Any]]:
        """Fetch recent Federal Register documents from relevant agencies."""
        items = []
        start_date = (date.today() - timedelta(days=self.days_back)).isoformat()
        for agency in self.AGENCY_QUERIES:
            try:
                url = f"{self.API_BASE}/documents.json"
                params = {
                    "per_page": 20,
                    "order": "newest",
                    "conditions[publication_date][gte]": start_date,
                    "conditions[agencies][]": agency,
                }
                resp = await self.client.get(url, params=params)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for doc in data.get("results", []):
                    doc["_agency_slug"] = agency
                    items.append(doc)
            except Exception as e:
                print(f"  Error fetching Federal Register for {agency}: {e}")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        """Transform a Federal Register document into NormalizedContent."""
        title = (item.get("title") or "").strip()
        abstract = (item.get("abstract") or "").strip()
        doc_number = item.get("document_number", "")
        agency_slug = item.get("_agency_slug", "")
        pub_date = item.get("publication_date", "")
        html_url = item.get("html_url", "")

        if not title:
            return None

        body = f"Title: {title}\n"
        body += f"Document Number: {doc_number}\n"
        body += f"Publication Date: {pub_date}\n"
        body += f"Agency: {item.get('agency_names', [''])[0]}\n"
        body += f"Type: {item.get('type', '')}\n"
        if abstract:
            body += f"\n{abstract}"

        topic = self.TOPIC_MAP.get(agency_slug, "federal-register")
        title_upper = title.upper()

        if "VICTIM" in title_upper or "OVC" in title_upper:
            topic = "ovc"
        elif "TRIBAL" in title_upper or "INDIAN" in title_upper or "NATIVE" in title_upper:
            topic = "tribal-funding"
        elif "GRANT" in title_upper or "CFR" in title_upper or "OMB" in title_upper:
            topic = "grant-regulations"
        elif "DEADLINE" in title_upper or "APPLICATION" in title_upper:
            topic = "compliance-deadlines"

        return NormalizedContent(
            url=html_url or f"https://www.federalregister.gov/d/{doc_number}",
            title=title,
            body_text=body,
            publish_date=None,
            metadata={
                "source": "federal_register",
                "document_number": doc_number,
                "agency": item.get("agency_names", [""])[0],
                "publication_date": pub_date,
                "doc_type": item.get("type", ""),
                "topic": topic,
                "query_category": topic,
            },
        )

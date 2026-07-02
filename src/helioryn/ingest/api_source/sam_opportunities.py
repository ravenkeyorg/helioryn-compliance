import os
from datetime import date, timedelta
from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class SamOpportunitiesSource(BaseApiSource):
    """Ingest current grant/contract opportunities from SAM.gov Get Opportunities API."""

    API_BASE = "https://api.sam.gov/opportunities/v2/search"
    TOPIC_MAP = {
        "JUSTICE": "doj-grants",
        "VICTIM": "ovc",
        "TRIBAL": "tribal-funding",
        "INDIAN": "tribal-funding",
        "HOUSING": "hud-grants",
        "HEALTH": "hhs-grants",
        "ENVIRONMENTAL": "grant-regulations",
        "TRAINING": "grant-opportunities",
        "EDUCATION": "grant-opportunities",
    }

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.days_back = config.get("days_back", 7)
        self.ptype = config.get("ptype", "")
        self.set_aside = config.get("set_aside", "")

    async def fetch_items(self) -> list[dict[str, Any]]:
        self.api_key = await self.resolve_api_key()
        if not self.api_key:
            print("  WARNING: No API key for SAM.gov Opportunities. Add one in Admin > API Keys.")
            return []

        items = []
        start = (date.today() - timedelta(days=self.days_back)).strftime("%m/%d/%Y")
        end = date.today().strftime("%m/%d/%Y")

        params = {
            "api_key": self.api_key,
            "postedFrom": start,
            "postedTo": end,
            "limit": 100,
        }
        if self.ptype:
            params["ptype"] = self.ptype
        if self.set_aside:
            params["typeOfSetAside"] = self.set_aside

        offset = 0
        while True:
            try:
                params["offset"] = offset
                resp = await self.client.get(self.API_BASE, params=params)
                if resp.status_code != 200:
                    print(f"  SAM Opportunities error {resp.status_code}: {resp.text[:200]}")
                    break
                data = resp.json()
                results = data.get("opportunitiesData", [])
                if not results:
                    break
                items.extend(results)
                total = data.get("totalRecords", 0)
                if offset + len(results) >= total:
                    break
                offset += len(results)
            except Exception as e:
                print(f"  SAM Opportunities pagination error: {e}")
                break

        print(f"  Fetched {len(items)} opportunities from SAM.gov")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        notice_id = item.get("noticeId", "")
        title = item.get("title", "") or ""
        agency = item.get("fullParentPathName", "") or ""
        posted = item.get("postedDate", "")
        response_deadline = item.get("responseDeadLine", "")
        award_amount = item.get("award", {}).get("amount", "") if item.get("award") else ""
        description_url = item.get("description", "")
        set_aside = item.get("typeOfSetAsideDescription", "") or ""
        ui_link = item.get("uiLink", "") or f"https://sam.gov/opp/{notice_id}/view"

        if not title:
            return None

        body = f"Title: {title}\n"
        body += f"Notice ID: {notice_id}\n"
        body += f"Agency: {agency}\n"
        if posted:
            body += f"Posted: {posted}\n"
        if response_deadline:
            body += f"Response Deadline: {response_deadline}\n"
        if award_amount:
            body += f"Award Amount: ${award_amount}\n"
        if set_aside:
            body += f"Set-Aside: {set_aside}\n"
        body += f"\nView at: {description_url or ui_link}"

        text_upper = f"{title} {agency} {set_aside}".upper()
        topic = self.topic or "grant-opportunities"
        for keyword, mapped in self.TOPIC_MAP.items():
            if keyword in text_upper:
                topic = mapped
                break

        return NormalizedContent(
            url=ui_link,
            title=title,
            body_text=body,
            publish_date=None,
            metadata={
                "source": "sam_opportunities",
                "notice_id": notice_id,
                "agency": agency,
                "posted_date": posted,
                "response_deadline": response_deadline,
                "set_aside": set_aside,
                "topic": topic,
                "query_category": topic,
            },
        )

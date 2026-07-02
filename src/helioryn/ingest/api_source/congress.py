import os
from datetime import date
from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class CongressSource(BaseApiSource):
    """Ingest congressional data from Congress.gov API — bills, CRS reports, laws. Requires api.data.gov key."""

    API_BASE = "https://api.congress.gov/v3"
    TOPIC_KEYWORDS = {
        "VICTIM": "ovc",
        "OVC": "ovc",
        "VOCA": "ovc",
        "TRIBAL": "tribal-funding",
        "INDIAN": "tribal-funding",
        "NATIVE": "tribal-funding",
        "HOUSING": "hud-grants",
        "HEALTH": "hhs-grants",
        "IHS": "tribal-funding",
        "JUSTICE": "doj-grants",
        "GRANT": "grant-opportunities",
        "COMPLIANCE": "grant-regulations",
        "CFR": "grant-regulations",
        "APPROPRIATIONS": "grant-opportunities",
    }

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.congress = config.get("congress", 119)

    async def resolve_api_key(self) -> str:
        key = await super().resolve_api_key()
        return key or "DEMO_KEY"

    async def fetch_items(self) -> list[dict[str, Any]]:
        self.api_key = await self.resolve_api_key()
        items = []

        # Fetch bills
        offset = 0
        while offset < 500:
            try:
                resp = await self.client.get(
                    f"{self.API_BASE}/bill/{self.congress}",
                    params={
                        "api_key": self.api_key,
                        "format": "json",
                        "offset": offset,
                        "limit": 250,
                    },
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                bills = data.get("bills", [])
                if not bills:
                    break
                for bill in bills:
                    bill["_type"] = "bill"
                    items.append(bill)
                pagination = data.get("pagination", {})
                if not pagination.get("next"):
                    break
                offset += 250
            except Exception as e:
                print(f"  Congress.gov bills error: {e}")
                break

        # Fetch CRS reports
        offset = 0
        while offset < 250:
            try:
                resp = await self.client.get(
                    f"{self.API_BASE}/crsreport",
                    params={
                        "api_key": self.api_key,
                        "format": "json",
                        "offset": offset,
                        "limit": 250,
                    },
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                reports = data.get("reports", []) or data.get("crsReports", [])
                if not reports:
                    break
                for report in reports:
                    report["_type"] = "crsreport"
                    items.append(report)
                pagination = data.get("pagination", {})
                if not pagination.get("next"):
                    break
                offset += 250
            except Exception as e:
                print(f"  Congress.gov CRS error: {e}")
                break

        print(f"  Fetched {len(items)} items from Congress.gov")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        item_type = item.get("_type", "bill")

        if item_type == "bill":
            number = item.get("number", "")
            bill_type = item.get("type", "")
            congress = item.get("congress", "")
            title = item.get("title", "") or item.get("shortTitle", "") or ""
            origin_chamber = item.get("originChamber", "") or ""
            introduced = item.get("introducedDate", "") or item.get("date", "")
            latest_action = item.get("latestAction", {}) or {}
            latest_action_text = latest_action.get("text", "") if isinstance(latest_action, dict) else ""
            url = item.get("url", "") or f"https://www.congress.gov/bill/{congress}th-congress/{bill_type}/{number}"

            if not title:
                return None

            body = f"Bill: {bill_type.upper()} {number}\n"
            body += f"Congress: {congress}\n"
            body += f"Title: {title}\n"
            if introduced:
                body += f"Introduced: {introduced}\n"
            if origin_chamber:
                body += f"Chamber: {origin_chamber}\n"
            if latest_action_text:
                body += f"Latest Action: {latest_action_text}\n"

        elif item_type == "crsreport":
            report_number = item.get("reportNumber", "") or item.get("number", "")
            title = item.get("title", "") or item.get("shortTitle", "") or ""
            summary = item.get("summary", "") or item.get("abstract", "") or ""
            date_str = item.get("date", "") or item.get("publishedDate", "") or item.get("year", "")
            url = item.get("url", "") or f"https://www.congress.gov/crs-report/{report_number}"

            if not title:
                return None

            body = f"CRS Report: {report_number}\n"
            body += f"Title: {title}\n"
            if date_str:
                body += f"Date: {date_str}\n"
            if summary:
                body += f"\n{summary}"

        else:
            return None

        text_upper = f"{title}".upper()
        topic = self.topic or "grant-regulations"
        for keyword, mapped in self.TOPIC_KEYWORDS.items():
            if keyword in text_upper:
                topic = mapped
                break

        return NormalizedContent(
            url=url,
            title=title[:500] if title else "Congress Document",
            body_text=body,
            publish_date=None,
            metadata={
                "source": "congress_gov",
                "item_type": item_type,
                "topic": topic,
                "query_category": topic,
            },
        )

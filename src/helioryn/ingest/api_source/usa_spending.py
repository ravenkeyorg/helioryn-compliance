import os
from datetime import date, timedelta
from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class UsaSpendingSource(BaseApiSource):
    """Ingest federal grant awards from USAspending.gov API. No auth required."""

    API_BASE = "https://api.usaspending.gov"
    GRANT_CODES = ["02", "03", "04", "05"]
    AGENCY_MAP = {
        "015": "doj-grants",
        "075": "hhs-grants",
        "086": "hud-grants",
        "010": "tribal-funding",
        "068": "grant-regulations",
    }

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=60.0)
        self.days_back = config.get("days_back", 30)
        self.agency_code = config.get("agency_code", "")
        self.query_keyword = config.get("query", "")
        self.state_filter = config.get("state", "")

    async def fetch_items(self) -> list[dict[str, Any]]:
        items = []
        start_date = (date.today() - timedelta(days=self.days_back)).isoformat()
        end_date = date.today().isoformat()

        filters = {
            "award_type_codes": self.GRANT_CODES,
            "time_period": [{"start_date": start_date, "end_date": end_date}],
        }
        if self.agency_code:
            filters["agencies"] = [{"type": "funding", "tier": "toptier", "name": self.agency_code}]
        if self.query_keyword:
            filters["query"] = self.query_keyword
        if self.state_filter:
            filters["place_of_performance_locations"] = [{"country": "USA", "state": self.state_filter}]

        fields = [
            "Award ID", "Recipient Name", "Award Amount", "Awarding Agency",
            "Award Type", "Funding Sub Agency", "CFDA Numbers",
            "Start Date", "End Date", "Description",
            "Place of Performance State Code", "Recipient UEI",
        ]

        page = 1
        while True:
            try:
                resp = await self.client.post(
                    f"{self.API_BASE}/api/v2/search/spending_by_award/",
                    json={
                        "filters": filters,
                        "fields": fields,
                        "limit": 100,
                        "page": page,
                        "sort": "Award Amount",
                        "order": "desc",
                    },
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                results = data.get("results", [])
                items.extend(results)
                meta = data.get("page_metadata", {})
                if not meta.get("hasNext"):
                    break
                page += 1
            except Exception as e:
                print(f"  USAspending pagination error: {e}")
                break

        print(f"  Fetched {len(items)} grant awards from USAspending")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        award_id = item.get("Award ID", "") or item.get("generated_unique_award_id", "")
        title = item.get("Recipient Name", "") or ""
        desc = item.get("Description", "") or ""
        amount = item.get("Award Amount", 0) or 0
        agency = item.get("Awarding Agency", "") or ""
        cfda = item.get("CFDA Numbers", "") or ""
        state = item.get("Place of Performance State Code", "") or ""
        start = item.get("Start Date", "") or ""

        body = f"Award ID: {award_id}\n"
        body += f"Recipient: {title}\n"
        body += f"Agency: {agency}\n"
        body += f"Amount: ${amount:,.2f}\n"
        if cfda:
            body += f"CFDA: {cfda}\n"
        if state:
            body += f"State: {state}\n"
        if start:
            body += f"Start Date: {start}\n"
        body += f"\n{desc}"

        agency_upper = agency.upper()
        topic = self.topic or "grant-opportunities"
        for code, mapped in self.AGENCY_MAP.items():
            if code in agency_upper:
                topic = mapped
                break
        if "TRIBAL" in agency_upper or "INDIAN" in agency_upper:
            topic = "tribal-funding"

        return NormalizedContent(
            url=f"https://www.usaspending.gov/award/{award_id}" if award_id else "https://www.usaspending.gov",
            title=f"Grant Award: {title}" if not title.startswith("Grant") else title,
            body_text=body,
            publish_date=None,
            metadata={
                "source": "usa_spending",
                "award_id": award_id,
                "agency": agency,
                "amount": amount,
                "cfda": cfda,
                "state": state,
                "recipient": title,
                "topic": topic,
                "query_category": topic,
            },
        )

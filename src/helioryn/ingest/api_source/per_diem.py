from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class PerDiemSource(BaseApiSource):
    """Ingest GSA Per Diem rates for grant travel budget compliance."""

    API_BASE = "https://api.gsa.gov/travel/perdiem/v2/rates"
    STATES = ["AK", "CA", "DC", "VA", "MD", "CO", "WA", "OR"]

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.year = config.get("year", 2025)

    async def resolve_api_key(self) -> str:
        key = await super().resolve_api_key()
        return key or ""

    async def fetch_items(self) -> list[dict[str, Any]]:
        api_key = await self.resolve_api_key()
        if not api_key:
            print("  WARNING: No API key for GSA Per Diem. Add one in Admin > API Keys.")
            return []
        items = []
        for state in self.STATES:
            try:
                resp = await self.client.get(
                    f"{self.API_BASE}/state/{state}/year/{self.year}",
                    params={"api_key": api_key},
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                rates = data if isinstance(data, list) else data.get("rates", []) if isinstance(data, dict) else []
                for r in rates:
                    if isinstance(r, dict):
                        r["_state"] = state
                        items.append(r)
            except Exception as e:
                print(f"  Per Diem error for {state}: {e}")
        print(f"  Fetched {len(items)} per diem rate entries from GSA")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        state = item.get("_state", "")
        city = item.get("city", "") or item.get("location", "") or ""
        lodging = item.get("lodging", "") or item.get("Lodging", "") or item.get("lodging_rate", "")
        meals = item.get("meals", "") or item.get("M_and_IE", "") or item.get("meals_and_incidentals", "")
        effective = item.get("effective_date", "") or item.get("start_date", "") or ""

        if not city and not state:
            return None

        body = f"Location: {city}, {state}\n"
        if lodging:
            body += f"Lodging: ${lodging}\n"
        if meals:
            body += f"M&IE: ${meals}\n"
        if effective:
            body += f"Effective: {effective}\n"

        return NormalizedContent(
            url=f"https://www.gsa.gov/travel/plan-book/per-diem-rates",
            title=f"Per Diem Rates — {city}, {state}" if city else f"Per Diem Rates — {state}",
            body_text=body,
            publish_date=None,
            metadata={
                "source": "gsa_per_diem",
                "state": state,
                "city": city,
                "topic": "grant-regulations",
                "query_category": "grant-regulations",
            },
        )

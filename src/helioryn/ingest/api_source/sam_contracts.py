import os
from datetime import date, timedelta
from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class SamContractsSource(BaseApiSource):
    """Ingest federal contract awards from SAM.gov Contract Awards API, focusing on tribal/Indian set-asides."""

    API_BASE = "https://api.sam.gov/contract-awards/v1/search"
    SET_ASIDE_KEYWORDS = [
        "INDIAN ECONOMIC ENTERPRISE",
        "BUY INDIAN",
        "INDIAN SMALL BUSINESS",
        "INDIAN",
        "TRIBAL",
        "NATIVE",
    ]

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.days_back = config.get("days_back", 30)
        self.set_aside_filter = config.get("set_aside", "")
        self.agency_filter = config.get("agency", "")

    async def fetch_items(self) -> list[dict[str, Any]]:
        self.api_key = await self.resolve_api_key()
        if not self.api_key:
            print("  WARNING: No API key for SAM.gov Contracts. Add one in Admin > API Keys.")
            return []

        items = []
        start = (date.today() - timedelta(days=self.days_back)).isoformat()
        end = date.today().isoformat()

        params = {
            "api_key": self.api_key,
            "limit": 100,
            "offset": 0,
        }
        if self.set_aside_filter:
            params["typeOfSetAsideName"] = self.set_aside_filter
        else:
            params["q"] = "INDIAN OR TRIBAL OR NATIVE"
        if self.agency_filter:
            params["contractingDepartmentName"] = self.agency_filter

        while True:
            try:
                resp = await self.client.get(self.API_BASE, params=params)
                if resp.status_code != 200:
                    print(f"  SAM Contracts error {resp.status_code}: {resp.text[:200]}")
                    break
                data = resp.json()
                results = data.get("results", []) or data.get("contractsData", []) or data.get("data", [])
                if not results:
                    break
                items.extend(results)
                if len(results) < 100:
                    break
                params["offset"] += len(results)
            except Exception as e:
                print(f"  SAM Contracts pagination error: {e}")
                break

        print(f"  Fetched {len(items)} contract awards from SAM.gov")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        piid = item.get("piid", "") or item.get("solicitationNumber", "") or ""
        awardee = item.get("awardee", {}) or {}
        awardee_name = awardee.get("name", "") if isinstance(awardee, dict) else ""
        agency = item.get("contractingDepartmentName", "") or item.get("agency", "") or ""
        amount = item.get("dollarsObligated", 0) or 0
        naics = item.get("naicsCode", "") or ""
        psc = item.get("productOrServiceCode", "") or ""
        signed_date = item.get("dateSigned", "") or item.get("signedDate", "")
        description = item.get("description", "") or item.get("contractDescription", "") or ""

        if not piid:
            return None

        body = f"Contract: {piid}\n"
        body += f"Awardee: {awardee_name}\n"
        body += f"Agency: {agency}\n"
        body += f"Amount: ${amount:,.2f}\n"
        if naics:
            body += f"NAICS: {naics}\n"
        if signed_date:
            body += f"Signed: {signed_date}\n"
        if description:
            body += f"\n{description}"

        topic = self.topic or "grant-opportunities"
        text_upper = f"{awardee_name} {agency} {description}".upper()
        for kw in self.SET_ASIDE_KEYWORDS:
            if kw in text_upper:
                topic = "tribal-funding"
                break

        return NormalizedContent(
            url=f"https://sam.gov/contract/{piid}/view" if piid else "https://sam.gov",
            title=f"Contract: {piid} — {awardee_name}" if awardee_name else f"Contract: {piid}",
            body_text=body,
            publish_date=None,
            metadata={
                "source": "sam_contracts",
                "piid": piid,
                "awardee": awardee_name,
                "agency": agency,
                "amount": amount,
                "naics": naics,
                "topic": topic,
                "query_category": topic,
            },
        )

from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class EpaEchoSource(BaseApiSource):
    """Ingest environmental compliance data from EPA ECHO API. No auth required."""

    API_BASE = "https://echo.epa.gov/api/rest/v3"

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.state = config.get("state", "AK")
        self.query = config.get("query", "")

    async def fetch_items(self) -> list[dict[str, Any]]:
        items = []
        try:
            params = {
                "output": "JSON",
                "qprov": "helioryn",
                "p_st": self.state,
                "pagesize": 500,
            }
            if self.query:
                params["q"] = self.query
            resp = await self.client.get(f"{self.API_BASE}/facility", params=params)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("Results", [])
                for r in results:
                    r["_source"] = "facility"
                    items.append(r)
        except Exception as e:
            print(f"  EPA ECHO facility error: {e}")

        print(f"  Fetched {len(items)} facility records from EPA ECHO")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        name = item.get("FacilityName", "") or item.get("facility_name", "") or ""
        city = item.get("City", "") or ""
        state = item.get("State", "") or ""
        sic_code = item.get("SICCode", "") or item.get("sic_code", "") or ""
        compliance = item.get("ComplianceStatus", "") or item.get("compliance_status", "") or ""
        inspections = item.get("InspectionsCount", 0) or 0
        violations = item.get("ViolationsCount", 0) or 0
        penalties = item.get("PenaltiesCount", 0) or 0

        if not name:
            return None

        body = f"Facility: {name}\n"
        if city and state:
            body += f"Location: {city}, {state}\n"
        if compliance:
            body += f"Compliance: {compliance}\n"
        if inspections:
            body += f"Inspections: {inspections}\n"
        if violations:
            body += f"Violations: {violations}\n"
        if penalties:
            body += f"Penalties: {penalties}\n"

        return NormalizedContent(
            url=f"https://echo.epa.gov/facilities/{name.replace(' ', '-')}" if name else "https://echo.epa.gov",
            title=f"EPA Facility: {name}",
            body_text=body,
            publish_date=None,
            metadata={
                "source": "epa_echo",
                "facility_name": name,
                "state": state,
                "compliance_status": compliance,
                "topic": "grant-regulations",
                "query_category": "grant-regulations",
            },
        )

# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
import io
import zipfile
from datetime import date, timedelta
from typing import Any
import httpx
import xml.etree.ElementTree as ET
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class GrantsGovSource(BaseApiSource):
    """Ingest grant opportunities from the daily Grants.gov XML extract."""

    EXTRACT_URL = "https://prod-grants-gov-chatbot.s3.amazonaws.com/extracts/GrantsDBExtract{date}v2.zip"
    TOPIC_MAP = {
        "OVC": "ovc",
        "VOCA": "ovc",
        "VICTIM": "ovc",
        "DOJ": "doj-grants",
        "BJA": "doj-grants",
        "OJJDP": "doj-grants",
        "NIJ": "doj-grants",
        "COPS": "doj-grants",
        "JUSTICE": "doj-grants",
        "IHS": "tribal-funding",
        "NAHASDA": "tribal-funding",
        "BIA": "tribal-funding",
        "INDIAN": "tribal-funding",
        "TRIBAL": "tribal-funding",
        "HHS": "hhs-grants",
        "ACF": "hhs-grants",
        "SAMHSA": "hhs-grants",
        "HRSA": "hhs-grants",
        "HUD": "hud-grants",
        "CDBG": "hud-grants",
        "HOME": "hud-grants",
    }
    DEFAULT_TOPIC = "grant-opportunities"

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=120.0, follow_redirects=True)

    async def fetch_items(self) -> list[dict[str, Any]]:
        """Download and parse the daily Grants.gov XML extract."""
        try:
            today = date.today()
            url = self.EXTRACT_URL.format(date=today.strftime("%Y%m%d"))
            resp = await self.client.get(url)
            if resp.status_code != 200:
                yesterday = today - timedelta(days=1)
                url = self.EXTRACT_URL.format(date=yesterday.strftime("%Y%m%d"))
                resp = await self.client.get(url)
                if resp.status_code != 200:
                    print(f"  Grants.gov extract not available for today or yesterday")
                    return []
            items = self._parse_xml(resp.content)
            print(f"  Parsed {len(items)} grant opportunities from {url.split('/')[-1]}")
            return items
        except Exception as e:
            print(f"  Error fetching grants.gov extract: {e}")
            return []

    def _parse_xml(self, content: bytes) -> list[dict]:
        """Parse the Grants.gov XML extract zip into opportunity dicts."""
        items = []
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                xml_files = [n for n in zf.namelist() if n.endswith(".xml")]
                if not xml_files:
                    return items
                for xml_name in xml_files:
                    raw = zf.read(xml_name)
                    items.extend(self._parse_xml_content(raw))
        except zipfile.BadZipFile:
            pass
        return items

    def _parse_xml_content(self, raw: bytes) -> list[dict]:
        """Parse XML content into opportunity dicts."""
        import xml.etree.ElementTree as ET
        items = []
        try:
            root = ET.fromstring(raw)
            ns_val = root.tag.split("}")[0][1:] if "}" in root.tag else ""
            ns = {"g": ns_val}

            opps = root.findall(".//g:OpportunitySynopsisDetail_1_0", ns)
            for opp in opps:
                item = {}
                item["oppNumber"] = self._find_text(opp, "g:OpportunityID", ns)
                item["title"] = self._find_text(opp, "g:OpportunityTitle", ns)
                item["agency"] = self._find_text(opp, "g:AgencyName", ns)
                item["agencyCode"] = self._find_text(opp, "g:AgencyCode", ns)
                item["cfda"] = self._find_text(opp, "g:CFDANumbers", ns)
                item["openDate"] = self._find_text(opp, "g:PostDate", ns)
                item["closeDate"] = self._find_text(opp, "g:CloseDate", ns)
                item["oppStatus"] = self._find_text(opp, "g:OpportunityStatus", ns)
                item["estimatedTotalFunding"] = self._find_text(opp, "g:EstimatedTotalProgramFunding", ns)
                item["awardCeiling"] = self._find_text(opp, "g:AwardCeiling", ns)
                item["awardFloor"] = self._find_text(opp, "g:AwardFloor", ns)
                item["description"] = self._find_text(opp, "g:Description", ns)
                item["eligibility"] = self._find_text(opp, "g:EligibleApplicants", ns)
                if item.get("oppNumber"):
                    items.append(item)
        except ET.ParseError:
            pass
        return items

    def _find_text(self, parent: ET.Element, xpath: str, ns: dict) -> str:
        """Find an element by XPath and return its text content, or empty string."""
        el = parent.find(xpath, ns)
        return el.text.strip() if el is not None and el.text else ""

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        """Transform a grants.gov opportunity into NormalizedContent."""
        title = item.get("title", "").strip()
        opp_number = item.get("oppNumber", "")
        agency = item.get("agency", "")
        description = item.get("description", "").strip()
        close_date = item.get("closeDate", "")
        funding = item.get("estimatedTotalFunding", "")
        if not title and not description:
            return None

        body = f"Title: {title}\n"
        body += f"Agency: {agency}\n"
        body += f"Opportunity Number: {opp_number}\n"
        if close_date:
            body += f"Close Date: {close_date}\n"
        if funding:
            body += f"Estimated Total Funding: ${funding}\n"
        if item.get("awardCeiling"):
            body += f"Award Ceiling: ${item['awardCeiling']}\n"
        if item.get("awardFloor"):
            body += f"Award Floor: ${item['awardFloor']}\n"
        body += f"\n{description}"

        url = f"https://www.grants.gov/view-opportunity.html?oppId={opp_number}"

        agency_upper = (agency or "").upper()
        topic = self.DEFAULT_TOPIC
        for keyword, mapped_topic in self.TOPIC_MAP.items():
            if keyword in agency_upper or keyword in (title or "").upper():
                topic = mapped_topic
                break

        return NormalizedContent(
            url=url,
            title=title,
            body_text=body,
            publish_date=None,
            metadata={
                "source": "grants.gov",
                "opportunity_number": opp_number,
                "agency": agency,
                "cfda": item.get("cfda", ""),
                "close_date": close_date,
                "funding": funding,
                "status": item.get("oppStatus", ""),
                "topic": topic,
                "query_category": topic,
            },
        )

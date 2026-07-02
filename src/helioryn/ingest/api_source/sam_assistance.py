import os
from typing import Any
import httpx
from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.models import NormalizedContent


class SamAssistanceSource(BaseApiSource):
    """Ingest SAM.gov Assistance Listings (CFDA catalog replacement) — full grant program metadata."""

    API_BASE = "https://api.sam.gov/assistance-listings/v1/search"
    KEYWORD_TOPIC_MAP = {
        "VICTIM": "ovc",
        "OVC": "ovc",
        "VOCA": "ovc",
        "TRIBAL": "tribal-funding",
        "INDIAN": "tribal-funding",
        "NATIVE": "tribal-funding",
        "HOUSING": "hud-grants",
        "CDBG": "hud-grants",
        "HEALTH": "hhs-grants",
        "SUBSTANCE": "hhs-grants",
        "MENTAL": "hhs-grants",
        "JUSTICE": "doj-grants",
        "LAW ENFORCEMENT": "doj-grants",
        "CFR": "grant-regulations",
        "COMPLIANCE": "grant-regulations",
    }

    def __init__(self, config: dict, ingestor, store):
        super().__init__(config, ingestor, store)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.applicant_types = config.get("applicant_types", "")
        self.assistance_types = config.get("assistance_types", "")

    async def fetch_items(self) -> list[dict[str, Any]]:
        self.api_key = await self.resolve_api_key()
        if not self.api_key:
            print("  WARNING: No API key for SAM.gov Assistance Listings. Add one in Admin > API Keys.")
            return []

        items = []
        page = 1
        params = {
            "api_key": self.api_key,
            "status": "Active",
            "pageSize": 100,
        }
        if self.applicant_types:
            params["applicantTypes"] = self.applicant_types
        if self.assistance_types:
            params["assistanceTypes"] = self.assistance_types

        while True:
            try:
                params["pageNumber"] = page
                resp = await self.client.get(self.API_BASE, params=params)
                if resp.status_code != 200:
                    print(f"  SAM Assistance Listings error {resp.status_code}: {resp.text[:200]}")
                    break
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    break
                items.extend(results)
                total_pages = data.get("totalPages", 1)
                if page >= total_pages:
                    break
                page += 1
            except Exception as e:
                print(f"  SAM Assistance pagination error: {e}")
                break

        print(f"  Fetched {len(items)} assistance listings from SAM.gov")
        return items

    def item_to_normalized(self, item: dict[str, Any]) -> NormalizedContent | None:
        program_number = item.get("assistanceListingId", "") or item.get("programNumber", "")
        program_title = item.get("programTitle", "") or item.get("title", "") or ""
        agency = item.get("agencyName", "") or item.get("agency", "") or ""
        description = item.get("description", "") or ""
        objectives = item.get("programObjectives", "") or ""
        cfda_refs = item.get("cfdaReferences", "") or ""
        applicant_eligibility = item.get("applicantEligibility", "") or ""
        beneficiary_eligibility = item.get("beneficiaryEligibility", "") or ""
        compliance_requirements = item.get("complianceRequirements", "") or ""
        awarding_frequency = item.get("awardingFrequency", "") or ""
        formula_matching = item.get("formulaAndMatchingRequirements", "") or ""

        if not program_title and not description:
            return None

        body = f"Program: {program_title}\n"
        body += f"Program Number: {program_number}\n"
        body += f"Agency: {agency}\n"
        if cfda_refs:
            body += f"CFDA: {cfda_refs}\n"
        if awarding_frequency:
            body += f"Awarding Frequency: {awarding_frequency}\n"
        if applicant_eligibility:
            body += f"Applicant Eligibility: {applicant_eligibility}\n"
        if beneficiary_eligibility:
            body += f"Beneficiary Eligibility: {beneficiary_eligibility}\n"
        if formula_matching:
            body += f"Formula/Matching: {formula_matching}\n"
        if objectives:
            body += f"\nObjectives: {objectives}\n"
        if compliance_requirements:
            body += f"\nCompliance Requirements: {compliance_requirements}\n"
        if description:
            body += f"\n{description}"

        text_upper = f"{program_title} {agency} {description}".upper()
        topic = self.topic or "grant-opportunities"
        for keyword, mapped in self.KEYWORD_TOPIC_MAP.items():
            if keyword in text_upper:
                topic = mapped
                break

        return NormalizedContent(
            url=f"https://sam.gov/fal/{program_number}/view" if program_number else "https://sam.gov",
            title=program_title,
            body_text=body,
            publish_date=None,
            metadata={
                "source": "sam_assistance_listings",
                "program_number": program_number,
                "agency": agency,
                "applicant_eligibility": applicant_eligibility,
                "beneficiary_eligibility": beneficiary_eligibility,
                "topic": topic,
                "query_category": topic,
            },
        )

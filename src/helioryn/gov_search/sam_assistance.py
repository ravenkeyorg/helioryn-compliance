"""SAM.gov Assistance Listings search — program compliance requirements.

Requires a SAM.gov API key. Falls back gracefully if none is configured.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sam.gov/assistance-listings/v1/search"

# VOCA-relevant assistance listing numbers
VOCA_PROGRAMS: dict[str, str] = {
    "16.575": "Crime Victim Assistance (VOCA Formula)",
    "16.582": "Crime Victim Assistance/Discretionary Grants (OVC)",
    "16.041": "Tribal Victim Services Set-Aside Formula Program",
    "16.017": "Sexual Assault Services Formula Program (SASP)",
    "93.671": "Family Violence Prevention & Services",
}


def _get_api_key() -> str | None:
    """Resolve SAM.gov API key: env var > none."""
    return os.environ.get("SAM_GOV_API_KEY") or None


async def sam_assistance_search(
    program_number: str | None = None,
    topic: str | None = None,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Search SAM.gov assistance listings for program compliance requirements.

    Returns normalized result dicts.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.info("No SAM.gov API key — skipping SAM Assistance search")
        return []

    results: list[dict[str, str]] = []

    if program_number and program_number not in VOCA_PROGRAMS:
        VOCA_PROGRAMS[program_number] = f"ALN {program_number}"

    target_programs = (
        [program_number] if program_number else list(VOCA_PROGRAMS.keys())
    )

    for aln in target_programs:
        data = await _fetch_program(aln, api_key)
        if not data:
            continue
        result = _format_program(data, aln)
        if result:
            results.append(result)

    return results[:limit]


async def _fetch_program(aln: str, api_key: str) -> dict[str, Any] | None:
    """Fetch a single assistance listing by ALN."""
    params: dict[str, str] = {
        "api_key": api_key,
        "programNumber": aln,
        "pageNumber": "1",
        "pageSize": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                listings = data.get("results") or data.get("data") or data.get("_embedded", {}).get("results", [])
                if isinstance(listings, list) and listings:
                    return listings[0]
            return None
    except Exception as e:
        logger.debug("SAM Assistance API error for %s: %s", aln, e)
        return None


def _format_program(data: dict[str, Any], aln: str) -> dict[str, str] | None:
    """Format a SAM Assistance listing into our normalized result format."""
    title = data.get("programTitle") or data.get("title") or VOCA_PROGRAMS.get(aln, f"ALN {aln}")
    description = (
        data.get("objective") or data.get("description") or data.get("summary") or ""
    )
    agency = data.get("agencyName") or data.get("agency") or ""
    applicant_eligibility = data.get("applicantEligibility") or ""
    beneficiary_eligibility = data.get("beneficiaryEligibility") or ""
    compliance_info = data.get("complianceRequirements") or data.get("requirements") or ""

    text_parts = [f"Program: {title} (ALN {aln})"]
    if agency:
        text_parts.append(f"Agency: {agency}")
    if description:
        text_parts.append(f"\nObjective: {description[:500]}")
    if applicant_eligibility:
        text_parts.append(f"\nApplicant Eligibility: {applicant_eligibility[:300]}")
    if beneficiary_eligibility:
        text_parts.append(f"\nBeneficiary Eligibility: {beneficiary_eligibility[:300]}")
    if compliance_info:
        text_parts.append(f"\nCompliance Requirements: {compliance_info[:500]}")

    url = f"https://www.sam.gov/assistance-listings/details/{aln.replace('.', '')}"

    return {
        "title": f"SAM Assistance Listing {aln}: {title}",
        "text": "\n".join(text_parts)[:2000],
        "url": url,
        "source_type": "sam_assistance",
        "source_name": "SAM.gov Assistance Listings",
    }

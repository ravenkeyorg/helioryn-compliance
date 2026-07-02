"""Federal Audit Clearinghouse (FAC) on-demand search.

Queries the FAC API (PostgREST) for single audit findings.
Works with DEMO_KEY for development — no API key needed.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.fac.gov"
API_KEY = "DEMO_KEY"
HEADERS = {"X-Api-Key": API_KEY}

# Compliance requirement type labels (from OMB Compliance Supplement)
TYPE_LABELS: dict[str, str] = {
    "A": "Activities Allowed / Unallowed",
    "B": "Allowable Costs / Cost Principles",
    "C": "Cash Management",
    "D": "Eligibility",
    "E": "Equipment / Real Property Management",
    "F": "Matching / Level of Effort / Earmarking",
    "G": "Period of Performance",
    "H": "Procurement / Suspension & Debarment",
    "I": "Program Income",
    "J": "Reporting",
    "K": "Subrecipient Monitoring",
    "L": "Special Tests & Provisions",
}

VOCA_ALNS = ("575", "582")


def _type_label(code: str) -> str:
    return TYPE_LABELS.get(code.upper(), f"Compliance Type {code}")


async def fac_search(query: str, limit: int = 10) -> list[dict[str, str]]:
    """Search FAC findings_text for VOCA/OVC audit findings matching the query.

    Returns normalized result dicts.
    """
    results: list[dict[str, str]] = []

    # Step 1: Get federal_awards for 16.575/16.582 with findings
    awards = await _fetch_awards_with_findings(limit=limit * 2)
    if not awards:
        return results

    # Step 2: For each finding, fetch findings_text
    text_tasks = []
    for award in awards:
        findings = award.get("findings", []) or []
        gen = award.get("general") or {}
        auditee = (gen.get("auditee_name") or "Unknown") if isinstance(gen, dict) else "Unknown"
        audit_year = (gen.get("audit_year") or "") if isinstance(gen, dict) else ""
        aln = f"16.{award.get('federal_award_extension', '')}"

        for finding in findings[:3]:
            if not isinstance(finding, dict):
                continue
            ref = finding.get("reference_number", "")
            if not ref:
                continue
            text_tasks.append(_fetch_finding_text(
                award["report_id"], ref, auditee, audit_year, aln, finding
            ))

    # Fetch all finding texts in parallel
    texts = await _gather_with_limit(text_tasks, concurrency=5)
    seen_refs: set[str] = set()

    keyword_lower = query.lower()
    for finding_text, meta in texts:
        # Filter by keyword relevance
        if keyword_lower and keyword_lower not in finding_text.lower():
            continue
        dedup_key = f"{meta['report_id']}-{meta['ref']}"
        if dedup_key in seen_refs:
            continue
        seen_refs.add(dedup_key)

        type_req = meta.get("type_requirement", "")
        type_label = _type_label(type_req) if type_req else ""
        severity = []
        if meta.get("is_material_weakness") == "Y":
            severity.append("Material Weakness")
        if meta.get("is_significant_deficiency") == "Y":
            severity.append("Significant Deficiency")
        if meta.get("is_questioned_costs") == "Y":
            severity.append("Questioned Costs")
        if meta.get("is_repeat_finding") == "Y":
            severity.append("Repeat Finding")

        title = (
            f"FAC Audit {meta['audit_year']}: {meta['auditee']} — "
            f"ALN {meta['aln']} — {type_label}"
        )

        text = f"Finding: {title}\n"
        if severity:
            text += f"Severity: {', '.join(severity)}\n"
        text += finding_text[:2000]

        url = (
            f"{BASE_URL}/findings_text?report_id=eq.{meta['report_id']}"
            f"&finding_ref_number=eq.{meta['ref']}"
        )

        results.append({
            "title": title[:200],
            "text": text[:2000],
            "url": url,
            "source_type": "fac",
            "source_name": "Federal Audit Clearinghouse",
        })

        if len(results) >= limit:
            break

    return results


async def _fetch_awards_with_findings(limit: int = 20) -> list[dict[str, Any]]:
    """Fetch federal awards for VOCA/OVC programs that have audit findings."""
    url = f"{BASE_URL}/federal_awards"
    params = {
        "federal_agency_prefix": "eq.16",
        "federal_award_extension": f"in.({','.join(VOCA_ALNS)})",
        "findings_count": "gt.0",
        "select": (
            "report_id,award_reference,federal_program_name,amount_expended,"
            "findings_count,federal_agency_prefix,federal_award_extension,"
            "general(auditee_name,entity_type,audit_year),"
            "findings(award_reference,reference_number,type_requirement,"
            "is_material_weakness,is_questioned_costs,is_repeat_finding,"
            "is_significant_deficiency)"
        ),
        "limit": str(limit),
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()
            return [d for d in data if isinstance(d, dict)]
    except Exception as e:
        logger.warning("FAC API error fetching awards: %s", e)
        return []


async def _fetch_finding_text(
    report_id: str,
    ref: str,
    auditee: str,
    audit_year: str,
    aln: str,
    finding_meta: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Fetch the full text of a single audit finding."""
    url = f"{BASE_URL}/findings_text"
    params = {
        "report_id": f"eq.{report_id}",
        "finding_ref_number": f"eq.{ref}",
        "select": "finding_text",
        "limit": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data and isinstance(data[0], dict):
                text = data[0].get("finding_text", "") or ""
            else:
                text = ""
    except Exception as e:
        logger.debug("FAC finding text fetch error %s/%s: %s", report_id, ref, e)
        text = ""

    meta: dict[str, Any] = {
        "report_id": report_id,
        "ref": ref,
        "auditee": auditee,
        "audit_year": audit_year,
        "aln": aln,
        "type_requirement": finding_meta.get("type_requirement", ""),
        "is_material_weakness": finding_meta.get("is_material_weakness", "N"),
        "is_significant_deficiency": finding_meta.get("is_significant_deficiency", "N"),
        "is_questioned_costs": finding_meta.get("is_questioned_costs", "N"),
        "is_repeat_finding": finding_meta.get("is_repeat_finding", "N"),
    }
    return text, meta


async def _gather_with_limit(
    tasks: list[asyncio.Task],
    concurrency: int = 5,
) -> list[tuple[str, dict[str, Any]]]:
    """Run tasks with concurrency limit, return successful results only."""
    sem = asyncio.Semaphore(concurrency)

    async def _run(task):
        async with sem:
            return await task

    wrapped = [_run(t) for t in tasks]
    results = await asyncio.gather(*wrapped, return_exceptions=True)
    return [r for r in results if not isinstance(r, BaseException)]

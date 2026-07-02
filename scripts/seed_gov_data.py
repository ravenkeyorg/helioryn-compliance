#!/usr/bin/env python3
"""Seed government audit data from FAC API and DOJ OIG reports.

Usage:
    python scripts/seed_gov_data.py              # FAC only
    python scripts/seed_gov_data.py --oig        # FAC + download + import OIG
    python scripts/seed_gov_data.py --check      # check OIG updates

Inserts directly into source_snapshot, claim, claim_embedding.
Uses existing all-MiniLM-L6-v2 embedding model.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import trafilatura

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from helioryn.embed import generate_embedding

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

FAC_API_BASE = "https://api.fac.gov"
FAC_HEADERS = {"X-Api-Key": "DEMO_KEY"}

OIG_URLS = [
    ("https://oig.justice.gov/sites/default/files/reports/25-047.pdf",
     "Safe Horizon NYC — OVC Grant Audit (2025)"),
    ("https://oig.justice.gov/sites/default/files/reports/26-054.pdf",
     "Crisis Intervention Iowa — VOCA Grant Audit (2026)"),
    ("https://oig.justice.gov/sites/default/files/reports/24-030.pdf",
     "UM PGHC Maryland — OVC Grant Audit (2024)"),
    ("https://oig.justice.gov/sites/default/files/reports/20-100.pdf",
     "Virginia DCJS — VOCA Grant Management Audit (2020)"),
    ("https://oig.justice.gov/sites/default/files/reports/23-109.pdf",
     "J Bar J Oregon — Human Trafficking CSEC Audit (2023)"),
    ("https://oig.justice.gov/sites/default/files/reports/24-055.pdf",
     "Arizona DPS — OJP Victim Assistance Grant Audit (2024)"),
    ("https://oig.justice.gov/sites/default/files/reports/22-047.pdf",
     "Red Wind Consulting — OVW Cooperative Agreement Audit (2022)"),
    ("https://oig.justice.gov/sites/default/files/reports/21-069.pdf",
     "JustGrants Transition Impact Issue Alert (2021)"),
    ("https://oig.justice.gov/sites/default/files/reports/23-088.pdf",
     "VOCA Subrecipient Monitoring Audit (2023)"),
    ("https://oig.justice.gov/sites/default/files/reports/a1818.pdf",
     "DOJ Grant Award Closeout Process Audit (2018)"),
    ("https://oig.justice.gov/sites/default/files/reports/26-038.pdf",
     "Puerto Rico DOJ — OVC Victim Compensation Grant Audit (2026)"),
    ("https://oig.justice.gov/sites/default/files/reports/26-047.pdf",
     "Virginia DSS — VOCA Subaward Administration Audit (2026)"),
    ("https://oig.justice.gov/sites/default/files/reports/26-048.pdf",
     "Nebraska — OJP Victim Assistance Subrecipient Monitoring Risk Assessment (2026)"),
]

OIG_DIR = Path(__file__).resolve().parent.parent / "demo-data" / "oig-reports"


# ── DB helpers ──────────────────────────────────────────────────


async def _get_conn() -> asyncpg.Connection:
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "btaylor"
    return await asyncpg.connect(
        user=user, host="/tmp", database="helioryn_dev",
    )


async def _source_exists(conn, source_url: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM source_snapshot WHERE source_url = $1 LIMIT 1",
        source_url,
    )
    return row is not None


async def _insert_source(
    conn, source_id, source_url, title, author, retrieval_method, raw_text, metadata: dict | None = None,
):
    now = datetime.now(timezone.utc)
    import hashlib as _h
    content_hash = _h.md5(raw_text.encode()).hexdigest()
    await conn.execute(
        """INSERT INTO source_snapshot
           (source_id, source_url, title, author, retrieval_method,
            raw_text, content_hash, metadata, retrieved_at,
            first_seen_at, last_updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           ON CONFLICT (source_id) DO UPDATE SET
               last_updated_at = EXCLUDED.last_updated_at,
               raw_text = EXCLUDED.raw_text,
               metadata = EXCLUDED.metadata""",
        source_id, source_url, title, author, retrieval_method,
        raw_text, content_hash, json.dumps(metadata) if metadata else None,
        now, now, now,
    )


async def _insert_claim(conn, claim_id, source_id, source_url, canonical_text):
    now = datetime.now(timezone.utc)
    await conn.execute(
        """INSERT INTO claim
           (claim_id, source_id, source_url, canonical_text, original_text,
            extracted_at, current_version, extraction_method)
           VALUES ($1,$2,$3,$4,$4,$5,1,'seed_script')""",
        claim_id, source_id, source_url, canonical_text, now,
    )


async def _insert_embedding(conn, claim_id, embedding):
    await conn.execute(
        """INSERT INTO claim_embedding (claim_id, embedding, model_name)
           VALUES ($1, $2::vector, 'all-MiniLM-L6-v2')""",
        claim_id, json.dumps(embedding),
    )


# ── FAC Data ────────────────────────────────────────────────────


async def fetch_fac_findings(retries: int = 3) -> list[dict[str, Any]]:
    logger.info("Fetching FAC findings for 16.575/16.582...")
    url = f"{FAC_API_BASE}/federal_awards"
    params = {
        "federal_agency_prefix": "eq.16",
        "federal_award_extension": "in.(575,582)",
        "findings_count": "gt.0",
        "select": (
            "report_id,award_reference,federal_program_name,amount_expended,"
            "findings_count,federal_agency_prefix,federal_award_extension,"
            "general(auditee_name,entity_type,audit_year),"
            "findings(award_reference,reference_number,type_requirement,"
            "is_material_weakness,is_questioned_costs,is_repeat_finding,"
            "is_significant_deficiency)"
        ),
        "limit": "100",
    }
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params, headers=FAC_HEADERS)
                if resp.status_code == 429:
                    wait = 10 * (attempt + 1)
                    logger.info("  Rate limited (429), waiting %ds...", wait)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
            return [d for d in data if isinstance(d, dict)]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < retries - 1:
                wait = 10 * (attempt + 1)
                logger.info("  Rate limited, waiting %ds...", wait)
                await asyncio.sleep(wait)
                continue
            logger.warning("FAC fetch error (after %d retries): %s", attempt + 1, e)
            return []
        except Exception as e:
            logger.warning("FAC fetch error: %s", e)
            return []
    return []


async def fetch_finding_text(report_id: str, ref: str) -> str:
    url = f"{FAC_API_BASE}/findings_text"
    params = {
        "report_id": f"eq.{report_id}",
        "finding_ref_number": f"eq.{ref}",
        "select": "finding_text",
        "limit": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=FAC_HEADERS)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return data[0].get("finding_text", "") or ""
    except Exception:
        pass
    return ""


async def seed_fac() -> int:
    """Fetch and import FAC findings. Returns count imported."""
    conn = await _get_conn()
    try:
        awards = await fetch_fac_findings()
        if not awards:
            logger.warning("No FAC data fetched")
            return 0

        seen_texts: set[str] = set()
        count = 0

        for award in awards:
            gen = award.get("general") or {}
            if not isinstance(gen, dict):
                continue
            auditee = gen.get("auditee_name", "Unknown") or "Unknown"
            audit_year = gen.get("audit_year", "") or ""
            aln = f"16.{award.get('federal_award_extension', '')}"
            program = award.get("federal_program_name", "") or ""

            findings = award.get("findings") or []
            if isinstance(findings, dict):
                findings = [findings]

            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                ref = finding.get("reference_number", "")
                if not ref:
                    continue

                text = await fetch_finding_text(award["report_id"], ref)
                text = (text or "").strip()
                if len(text) < 40:
                    continue

                dedup = hashlib.md5(text.encode()).hexdigest()
                if dedup in seen_texts:
                    continue
                seen_texts.add(dedup)

                type_req = finding.get("type_requirement", "") or ""
                severity = []
                if finding.get("is_material_weakness") == "Y":
                    severity.append("material_weakness")
                if finding.get("is_significant_deficiency") == "Y":
                    severity.append("significant_deficiency")
                if finding.get("is_questioned_costs") == "Y":
                    severity.append("questioned_costs")
                if finding.get("is_repeat_finding") == "Y":
                    severity.append("repeat_finding")

                source_url = (
                    f"{FAC_API_BASE}/findings_text"
                    f"?report_id=eq.{award['report_id']}"
                    f"&finding_ref_number=eq.{ref}"
                )

                if await _source_exists(conn, source_url):
                    continue

                source_id = uuid4()
                title = (
                    f"FAC Audit {audit_year}: {auditee} — "
                    f"ALN {aln} ({program}) — Finding {ref}"
                )

                await _insert_source(
                    conn, source_id, source_url, title[:300],
                    "Federal Audit Clearinghouse", "gov_seed", text[:10000],
                    {
                        "aln": aln,
                        "audit_year": audit_year,
                        "auditee_name": auditee,
                        "finding_ref": ref,
                        "report_id": award["report_id"],
                        "type_requirement": type_req,
                        "severity": severity,
                        "findings_count": award.get("findings_count", 0),
                        "source": "fac",
                    },
                )

                first_bit = text.split(". ")[0] if ". " in text else text
                claim_text = first_bit[:500].strip().rstrip(".,")
                if claim_text:
                    claim_text += "."
                else:
                    claim_text = text[:200]

                claim_id = uuid4()
                await _insert_claim(conn, claim_id, source_id, source_url, claim_text)

                emb = generate_embedding(claim_text)
                await _insert_embedding(conn, claim_id, emb)

                count += 1
                if count % 20 == 0:
                    logger.info("  Imported %d FAC findings...", count)

        logger.info("FAC complete: %d findings", count)
        return count
    finally:
        await conn.close()


# ── DOJ OIG Reports ──────────────────────────────────────────────


async def _check_oig_update(url: str, local_path: Path) -> bool:
    if not local_path.exists():
        return True
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.head(url, follow_redirects=True)
            remote_size = int(resp.headers.get("content-length", 0))
            if remote_size != local_path.stat().st_size:
                return True
    except Exception:
        pass
    return False


async def download_oig_reports(check_only: bool = False) -> list[dict[str, Any]]:
    OIG_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for url, title in OIG_URLS:
        filename = url.rstrip("/").split("/")[-1].replace(".pdf", "")
        local_path = OIG_DIR / f"{filename}.pdf"

        if check_only:
            needs = await _check_oig_update(url, local_path)
            status = "UPDATE AVAILABLE" if needs else "up to date"
            logger.info("  %-20s %s", filename, status)
            if needs and not local_path.exists():
                results.append({"url": url, "title": title, "path": str(local_path)})
            continue

        if local_path.exists():
            logger.info("  Already cached: %s", filename)
            results.append({"url": url, "title": title, "path": str(local_path)})
            continue

        logger.info("  Downloading %s...", filename)
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                local_path.write_bytes(resp.content)
            logger.info("    Saved: %s (%d bytes)", local_path.name, len(resp.content))
            results.append({"url": url, "title": title, "path": str(local_path)})
        except Exception as e:
            logger.warning("    Failed: %s", e)
            continue

    return results


async def import_oig_report(conn, url: str, title: str, path: str) -> bool:
    """Import a single OIG PDF into source_snapshot. Returns True on success."""
    if await _source_exists(conn, url):
        logger.info("  Already imported: %s", title)
        return False

    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("  pypdf not installed — skipping")
        return False

    try:
        reader = PdfReader(path)
        text_parts = [p.extract_text() or "" for p in reader.pages]
        full_text = "\n".join(text_parts).strip()
        if len(full_text) < 100:
            logger.warning("  Too short (%d chars) — skipping", len(full_text))
            return False

        source_id = uuid4()
        await _insert_source(
            conn, source_id, url, title[:300],
            "DOJ Office of Inspector General", "gov_seed",
            full_text[:20000],
            {"source": "oig", "report_url": url, "oig_report_title": title},
        )

        # Extract major finding sections
        sections = []
        current: list[str] = []
        for line in full_text.split("\n"):
            s = line.strip()
            if not s:
                continue
            if (s.startswith("Finding No.") or s.startswith("Recommendation")
                    or "Finding" in s[:15].split(" ")):
                if current and len("\n".join(current)) > 60:
                    sections.append("\n".join(current))
                current = [s]
            else:
                current.append(s)
        if current and len("\n".join(current)) > 60:
            sections.append("\n".join(current))

        for i, section in enumerate(sections[:10]):
            claim_text = section[:500].strip()
            if len(claim_text) < 60:
                continue
            claim_id = uuid4()
            await _insert_claim(conn, claim_id, source_id, url, claim_text)
            emb = generate_embedding(claim_text)
            await _insert_embedding(conn, claim_id, emb)

        logger.info("  Imported %s", title)
        return True

    except Exception as e:
        logger.warning("  Error importing %s: %s", path, e)
        return False


async def seed_oig(check_only: bool = False, force: bool = False) -> int:
    """Download and import OIG reports. Returns count imported."""
    reports = await download_oig_reports(check_only=check_only)
    if check_only:
        return 0

    conn = await _get_conn()
    try:
        if force:
            await conn.execute("DELETE FROM claim_embedding WHERE model_name = 'all-MiniLM-L6-v2' AND claim_id IN (SELECT c.claim_id FROM claim c JOIN source_snapshot ss ON ss.source_id = c.source_id WHERE ss.retrieval_method = 'gov_seed')")
            await conn.execute(
                "DELETE FROM claim WHERE source_id IN "
                "(SELECT source_id FROM source_snapshot WHERE retrieval_method = 'gov_seed')",
            )
            await conn.execute(
                "DELETE FROM source_snapshot WHERE retrieval_method = 'gov_seed'",
            )
            logger.info("  Force-cleared existing gov_seed data")

        count = 0
        for report in reports:
            ok = await import_oig_report(conn, report["url"], report["title"], report["path"])
            if ok:
                count += 1
        return count
    finally:
        await conn.close()


# ── eCFR (Code of Federal Regulations) ───────────────────────────


CFR_URLS = [
    ("https://www.law.cornell.edu/cfr/text/2/200.1",
     "2 CFR Part 200 — Uniform Administrative Requirements, Cost Principles, and Audit Requirements for Federal Awards",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.303",
     "2 CFR § 200.303 — Internal Controls",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.330",
     "2 CFR § 200.330 — Subrecipient and Contractor Determinations",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.331",
     "2 CFR § 200.331 — Subrecipient and Contractor Requirements",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.332",
     "2 CFR § 200.332 — Subrecipient Monitoring",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.333",
     "2 CFR § 200.333 — Fixed Amount Subawards",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.343",
     "2 CFR § 200.343 — Closeout",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.500",
     "2 CFR § 200.500 — Audit Requirements — Subpart F",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.501",
     "2 CFR § 200.501 — Audit Thresholds",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.514",
     "2 CFR § 200.514 — Audit Findings",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/2/200.516",
     "2 CFR § 200.516 — Audit Resolution",
     "Uniform Guidance", "2"),
    ("https://www.law.cornell.edu/cfr/text/28/94.101",
     "28 CFR § 94.101 — VOCA: Purpose and Scope",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.102",
     "28 CFR § 94.102 — VOCA: Allocation of Funds",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.103",
     "28 CFR § 94.103 — VOCA: Grant Application and Award Process",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.104",
     "28 CFR § 94.104 — VOCA: Eligible Uses of Funds",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.105",
     "28 CFR § 94.105 — VOCA: Match Requirements",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.106",
     "28 CFR § 94.106 — VOCA: Administrative Costs",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.107",
     "28 CFR § 94.107 — VOCA: Reporting Requirements",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.108",
     "28 CFR § 94.108 — VOCA: Monitoring and Compliance",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.109",
     "28 CFR § 94.109 — VOCA: Allowable Costs",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.110",
     "28 CFR § 94.110 — VOCA: Subawards",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.111",
     "28 CFR § 94.111 — VOCA: Audit Requirements",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.112",
     "28 CFR § 94.112 — VOCA: Record Retention",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.113",
     "28 CFR § 94.113 — VOCA: Civil Rights Requirements",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.114",
     "28 CFR § 94.114 — VOCA: Drug-Free Workplace",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.115",
     "28 CFR § 94.115 — VOCA: Conflicts of Interest",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.116",
     "28 CFR § 94.116 — VOCA: Program Income",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.117",
     "28 CFR § 94.117 — VOCA: Real Property",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.118",
     "28 CFR § 94.118 — VOCA: Equipment",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.119",
     "28 CFR § 94.119 — VOCA: Supplies",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.120",
     "28 CFR § 94.120 — VOCA: Procurement",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.121",
     "28 CFR § 94.121 — VOCA: Property Management",
     "VOCA Regulations", "28"),
    ("https://www.law.cornell.edu/cfr/text/28/94.122",
     "28 CFR § 94.122 — VOCA: Closeout Requirements",
     "VOCA Regulations", "28"),
]


async def fetch_cfr_text(url: str) -> str | None:
    """Fetch and extract text from a Cornell LII CFR page."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = trafilatura.extract(resp.text)
            return text.strip() if text else None
    except Exception as e:
        logger.warning("  Failed to fetch %s: %s", url, e)
        return None


async def import_cfr_source(conn, url: str, title: str, topic: str, title_num: str) -> bool:
    """Import a CFR section as a source_snapshot row."""
    if await _source_exists(conn, url):
        return False

    text = await fetch_cfr_text(url)
    if not text or len(text) < 200:
        logger.warning("  Too short or empty: %s", title)
        return False

    source_id = uuid4()
    await _insert_source(
        conn, source_id, url, title[:300],
        "eCFR via Cornell LII", "gov_seed",
        text[:100000],
        {"source": "cfr", "cfr_title": title_num, "topic": topic, "regulation_url": url},
    )

    # Create overlapping chunks as separate claims for better retrieval
    chunk_size = 500
    overlap = 100
    seen_claims = set()
    for start in range(0, min(len(text), 5000), chunk_size - overlap):
        chunk = text[start:start + chunk_size].strip()
        if len(chunk) < 60:
            continue
        dedup_key = chunk[:80]
        if dedup_key in seen_claims:
            continue
        seen_claims.add(dedup_key)

        claim_id = uuid4()
        await _insert_claim(conn, claim_id, source_id, url, chunk)
        emb = generate_embedding(chunk)
        await _insert_embedding(conn, claim_id, emb)

    logger.info("  Imported: %s (%d chars)", title, len(text))
    return True


async def seed_cfr(force: bool = False) -> int:
    """Fetch and import CFR sections. Returns count imported."""
    conn = await _get_conn()
    try:
        if force:
            await conn.execute(
                "DELETE FROM claim_embedding WHERE model_name = 'all-MiniLM-L6-v2' "
                "AND claim_id IN (SELECT c.claim_id FROM claim c "
                "JOIN source_snapshot ss ON ss.source_id = c.source_id "
                "WHERE ss.retrieval_method = 'gov_seed' AND ss.metadata->>'source' = 'cfr')"
            )
            await conn.execute(
                "DELETE FROM claim WHERE source_id IN "
                "(SELECT source_id FROM source_snapshot WHERE retrieval_method = 'gov_seed' AND metadata->>'source' = 'cfr')"
            )
            await conn.execute(
                "DELETE FROM source_snapshot WHERE retrieval_method = 'gov_seed' AND metadata->>'source' = 'cfr'"
            )
            logger.info("  Force-cleared existing CFR data")

        count = 0
        for url, title, topic, title_num in CFR_URLS:
            ok = await import_cfr_source(conn, url, title, topic, title_num)
            if ok:
                count += 1
        return count
    finally:
        await conn.close()


# ── DOJ Grants Financial Guide ──────────────────────────────────


# ── OVC Program Guidance & Policy (ovc.ojp.gov) ──────────────

OVC_URLS = [
    ("https://ovc.ojp.gov/program/victims-crime-act-voca-administrators/voca-announcements",
     "OVC VOCA Announcements and Updates"),
    ("https://ovc.ojp.gov/program/victims-crime-act-voca-administrators/release-fy-2024-voca-formula-solicitations",
     "OVC FY2024 VOCA Formula Solicitations"),
    ("https://ovc.ojp.gov/program/victims-crime-act-voca-administrators/fy-2025-victim-compensation-certification-form",
     "OVC FY2025 Victim Compensation Certification"),
    ("https://ovc.ojp.gov/program/victims-crime-act-voca-administrators/fy-2023-voca-formula-solicitations-released",
     "OVC FY2023 VOCA Formula Solicitations"),
    ("https://ovc.ojp.gov/program/victims-crime-act-voca-administrators/spring-2023-updates",
     "OVC Spring 2023 VOCA Program Updates"),
    ("https://ovc.ojp.gov/about/crime-victims-fund/fy-2007-2026-cvf-balance.pdf",
     "Crime Victims Fund Balance FY2007-2026"),
    ("https://ovc.ojp.gov/about/crime-victims-fund/fy-2007-2026-cvf-annual-receipts.pdf",
     "Crime Victims Fund Annual Receipts FY2007-2026"),
    ("https://ovc.ojp.gov/program/victims-crime-act-voca-administrators/improve-project",
     "OVC IMPROVE Project — Grant Management Improvement"),
    ("https://ovc.ojp.gov/program/victims-crime-act-voca-administrators/2024-conference-and-open-house",
     "OVC 2024 VOCA Administrators Conference"),
    ("https://ovc.ojp.gov/program/victims-crime-act-voca-administrators/victim-assistance/vocapedia",
     "VOCA Victim Assistance Vocapedia — Program Guidance"),
]

FINANCIAL_GUIDE_URLS = [
    ("https://www.ojp.gov/funding/financialguidedoj/overview",
     "DOJ Grants Financial Guide — Overview"),
    ("https://www.ojp.gov/funding/financialguidedoj/i-general-information",
     "DOJ Grants Financial Guide — Part I: General Information"),
    ("https://www.ojp.gov/funding/financialguidedoj/ii-preaward-requirements",
     "DOJ Grants Financial Guide — Part II: Pre-Award Requirements"),
    ("https://www.ojp.gov/funding/financialguidedoj/iii-postaward-requirements",
     "DOJ Grants Financial Guide — Part III: Post-Award Requirements"),
    ("https://www.ojp.gov/funding/financialguidedoj/iv-organization-structure",
     "DOJ Grants Financial Guide — Part IV: Organizational Structure"),
    ("https://www.ojp.gov/funding/financialguidedoj/v-appendices",
     "DOJ Grants Financial Guide — Part V: Appendices"),
]


async def fetch_guide_text(url: str) -> str | None:
    """Fetch and extract text from OJP.gov Financial Guide page."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = trafilatura.extract(resp.text)
            return text.strip() if text else None
    except Exception as e:
        logger.warning("  Failed to fetch %s: %s", url, e)
        return None


async def import_guide_source(conn, url: str, title: str) -> bool:
    """Import a Financial Guide page as a source_snapshot row."""
    if await _source_exists(conn, url):
        return False

    text = await fetch_guide_text(url)
    if not text or len(text) < 200:
        logger.warning("  Too short or empty: %s", title)
        return False

    source_id = uuid4()
    await _insert_source(
        conn, source_id, url, title[:300],
        "DOJ Office of Justice Programs", "gov_seed",
        text[:20000],
        {"source": "financial_guide", "guide_url": url, "topic": "grant-regulations"},
    )

    first_bit = text.split(". ")[0] if ". " in text else text
    claim_text = first_bit[:500].strip().rstrip(".,")
    if claim_text:
        claim_text += "."
    else:
        claim_text = text[:200]

    claim_id = uuid4()
    await _insert_claim(conn, claim_id, source_id, url, claim_text)
    emb = generate_embedding(claim_text)
    await _insert_embedding(conn, claim_id, emb)

    logger.info("  Imported: %s (%d chars)", title, len(text))
    return True


async def seed_financial_guide(force: bool = False) -> int:
    """Fetch and import DOJ Grants Financial Guide. Returns count imported."""
    conn = await _get_conn()
    try:
        if force:
            await conn.execute(
                "DELETE FROM claim_embedding WHERE model_name = 'all-MiniLM-L6-v2' "
                "AND claim_id IN (SELECT c.claim_id FROM claim c "
                "JOIN source_snapshot ss ON ss.source_id = c.source_id "
                "WHERE ss.retrieval_method = 'gov_seed' AND ss.metadata->>'source' = 'financial_guide')"
            )
            await conn.execute(
                "DELETE FROM claim WHERE source_id IN "
                "(SELECT source_id FROM source_snapshot WHERE retrieval_method = 'gov_seed' AND metadata->>'source' = 'financial_guide')"
            )
            await conn.execute(
                "DELETE FROM source_snapshot WHERE retrieval_method = 'gov_seed' AND metadata->>'source' = 'financial_guide'"
            )
            logger.info("  Force-cleared existing Financial Guide data")

        count = 0
        for url, title in FINANCIAL_GUIDE_URLS:
            ok = await import_guide_source(conn, url, title)
            if ok:
                count += 1
        return count
    finally:
        await conn.close()


# ── OVC Program Pages ──────────────────────────────────────────


async def fetch_ovc_text(url: str) -> str | None:
    """Fetch and extract text from an OVC program page."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            if url.endswith(".pdf"):
                try:
                    from pypdf import PdfReader
                    import io
                    reader = PdfReader(io.BytesIO(resp.content))
                    text_parts = [p.extract_text() or "" for p in reader.pages]
                    return "\n".join(text_parts).strip()
                except ImportError:
                    return None
            text = trafilatura.extract(resp.text)
            return text.strip() if text else None
    except Exception as e:
        logger.warning("  Failed to fetch %s: %s", url, e)
        return None


async def import_ovc_source(conn, url: str, title: str) -> bool:
    """Import an OVC program page as a source_snapshot row."""
    if await _source_exists(conn, url):
        return False

    text = await fetch_ovc_text(url)
    if not text or len(text) < 100:
        logger.warning("  Too short or empty: %s", title)
        return False

    source_id = uuid4()
    stored_text = text[:100000]
    await _insert_source(
        conn, source_id, url, title[:300],
        "OVC — Office for Victims of Crime", "gov_seed",
        stored_text,
        {"source": "ovc_guidance", "guidance_url": url, "topic": "ovc"},
    )

    # Create overlapping chunks as separate claims for better retrieval
    chunk_size = 500
    overlap = 100
    seen_claims = set()
    for start in range(0, len(stored_text), chunk_size - overlap):
        chunk = stored_text[start:start + chunk_size].strip()
        # Filter out isolated headers / nav text
        if len(chunk) < 60:
            continue
        # Skip duplicate or near-duplicate chunks
        dedup_key = chunk[:80]
        if dedup_key in seen_claims:
            continue
        seen_claims.add(dedup_key)

        claim_id = uuid4()
        await _insert_claim(conn, claim_id, source_id, url, chunk)
        emb = generate_embedding(chunk)
        await _insert_embedding(conn, claim_id, emb)

    logger.info("  Imported: %s (%d chars, %d claims)", title, len(stored_text), len(seen_claims))
    return True


async def seed_ovc(force: bool = False) -> int:
    """Fetch and import OVC guidance pages. Returns count imported."""
    conn = await _get_conn()
    try:
        if force:
            await conn.execute(
                "DELETE FROM claim_embedding WHERE model_name = 'all-MiniLM-L6-v2' "
                "AND claim_id IN (SELECT c.claim_id FROM claim c "
                "JOIN source_snapshot ss ON ss.source_id = c.source_id "
                "WHERE ss.retrieval_method = 'gov_seed' AND ss.metadata->>'source' = 'ovc_guidance')"
            )
            await conn.execute(
                "DELETE FROM claim WHERE source_id IN "
                "(SELECT source_id FROM source_snapshot WHERE retrieval_method = 'gov_seed' AND metadata->>'source' = 'ovc_guidance')"
            )
            await conn.execute(
                "DELETE FROM source_snapshot WHERE retrieval_method = 'gov_seed' AND metadata->>'source' = 'ovc_guidance'"
            )
            logger.info("  Force-cleared existing OVC guidance data")

        count = 0
        for url, title in OVC_URLS:
            ok = await import_ovc_source(conn, url, title)
            if ok:
                count += 1
        return count
    finally:
        await conn.close()


# ── Main ────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="Seed government grant compliance data")
    parser.add_argument("--oig", action="store_true", help="Download and import OIG reports")
    parser.add_argument("--cfr", action="store_true", help="Fetch and import CFR (2 CFR 200 + 28 CFR 94)")
    parser.add_argument("--guide", action="store_true", help="Fetch and import DOJ Grants Financial Guide")
    parser.add_argument("--ovc", action="store_true", help="Fetch and import OVC guidance pages")
    parser.add_argument("--all", action="store_true", help="Seed all sources (FAC + OIG + CFR + Guide + OVC)")
    parser.add_argument("--check", action="store_true", help="Check OIG reports for updates")
    parser.add_argument("--force", action="store_true", help="Re-import even if already seeded")
    args = parser.parse_args()

    run_all = args.all or not (args.oig or args.cfr or args.guide or args.ovc or args.check)

    if run_all or not args.check:
        logger.info("Seeding FAC audit findings...")
        fac_count = await seed_fac()
        logger.info("Done: %d FAC findings imported\n", fac_count)

    if run_all or args.oig:
        logger.info("Downloading and importing OIG reports...")
        oig_count = await seed_oig(force=args.force)
        logger.info("Done: %d OIG reports imported\n", oig_count)
    elif args.check:
        logger.info("Checking OIG updates...")
        await seed_oig(check_only=True)
        return

    if run_all or args.cfr:
        logger.info("Fetching and importing CFR sections...")
        cfr_count = await seed_cfr(force=args.force)
        logger.info("Done: %d CFR sections imported\n", cfr_count)

    if run_all or args.guide:
        logger.info("Fetching and importing DOJ Grants Financial Guide...")
        guide_count = await seed_financial_guide(force=args.force)
        logger.info("Done: %d Financial Guide pages imported\n", guide_count)

    if run_all or args.ovc:
        logger.info("Fetching and importing OVC guidance pages...")
        ovc_count = await seed_ovc(force=args.force)
        logger.info("Done: %d OVC guidance pages imported\n", ovc_count)

    logger.info("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())

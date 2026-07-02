"""Government API search orchestrator — on-demand search for audit evidence."""

from __future__ import annotations

import asyncio
import logging

from helioryn.gov_search.fac import fac_search
from helioryn.gov_search.sam_assistance import sam_assistance_search

logger = logging.getLogger(__name__)


def _classify_gov_question(question: str) -> list[str]:
    q = question.lower()
    apis = []

    if any(w in q for w in (
        "finding", "audit", "compliance", "questioned cost",
        "deficiency", "material weakness", "noncompli",
        "single audit", "repeat finding", "oig",
        "what went wrong", "common issue", "frequent",
        "problem", "deficient", "gap", "violation",
    )):
        apis.append("fac")

    if any(w in q for w in (
        "requirement", "condition", "allowable", "eligible",
        "program", "cfda", "aln", "16.575", "16.582",
        "what are the", "what is the", "standard",
        "rule", "regulation", "policy",
    )):
        apis.append("sam_assistance")

    return apis


async def search_government(
    question: str,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Search relevant government APIs based on question classification.

    Returns list of dicts with keys: title, text, url, source_type, source_name
    """
    apis = _classify_gov_question(question)
    if not apis:
        return []

    tasks: dict[str, asyncio.Task] = {}
    if "fac" in apis:
        tasks["fac"] = asyncio.ensure_future(fac_search(question, limit=limit))
    if "sam_assistance" in apis:
        tasks["sam_assistance"] = asyncio.ensure_future(
            sam_assistance_search(topic=question, limit=limit)
        )

    if not tasks:
        return []

    done = await asyncio.gather(*tasks.values(), return_exceptions=True)

    merged: list[dict[str, str]] = []
    for api_name, result in zip(tasks.keys(), done):
        if isinstance(result, BaseException):
            logger.warning("gov_search %s error: %s", api_name, result)
            continue
        merged.extend(result)

    return merged

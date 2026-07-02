"""RAG output verification — lexical overlap claim checking against source evidence."""

from __future__ import annotations

import re

_NON_CLAIM_PATTERNS = re.compile(
    r"^(?:hello|hi there|welcome|sure|based on|in summary|"
    r"in conclusion|overall|here is|here would be|"
    r"i have found|i would recommend|please note|let me|"
    r"thank you|unfortunately|yes[!.]|no[!.]|great|okay|"
    r"the following|below|above)",
    re.IGNORECASE,
)

_META_CLAIM_PATTERNS = re.compile(
    r"(?:the (?:document|excerpt|source|context) (?:does|did) not|"
    r"no (?:information|mention|reference|evidence|data) was found|"
    r"it (?:is|was) (?:not|unclear|ambiguous|unable)|"
    r"i (?:cannot|could not|am unable|was unable)|"
    r"unfortunately|it appears|it seems|"
    r"the provided (?:context|documents|information) (?:does not|lack|fail))",
    re.IGNORECASE,
)

_MIN_CHARS = 20
_MAX_CHARS = 500


def _is_factual_claim(sentence: str) -> bool:
    s = sentence.strip()
    if len(s) < _MIN_CHARS or len(s) > _MAX_CHARS:
        return False
    if s.endswith("?"):
        return False
    if _NON_CLAIM_PATTERNS.match(s):
        return False
    if _META_CLAIM_PATTERNS.search(s):
        return False
    if s.startswith("-") or s.startswith("*") or s.startswith("#"):
        return False
    if s.startswith("•"):
        return False
    return True


def _split_sentences(text: str) -> list[str]:
    lines = text.split("\n")
    joined_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            joined_lines.append("")
            continue
        if joined_lines and joined_lines[-1] and not joined_lines[-1].endswith((".", "!", "?")):
            joined_lines[-1] = joined_lines[-1] + " " + stripped
        else:
            joined_lines.append(stripped)

    combined = " ".join(l for l in joined_lines if l)
    raw = re.split(r"(?<=[.!])\s+", combined)
    return [s.strip() for s in raw if s.strip()]


def _extract_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _extract_numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def _score_lexical_overlap(claim: str, source: str) -> float:
    """Score claim against source using token overlap + number matching (0.0–1.0)."""
    claim_tokens = _extract_tokens(claim)
    source_tokens = _extract_tokens(source)

    if not claim_tokens:
        return 0.0

    intersection = claim_tokens & source_tokens
    token_score = len(intersection) / len(claim_tokens)

    claim_nums = _extract_numbers(claim)
    num_score = 1.0
    if claim_nums:
        source_nums = _extract_numbers(source)
        covered = claim_nums & source_nums
        num_score = len(covered) / len(claim_nums)

    return 0.5 * token_score + 0.5 * num_score


async def verify_claims(
    answer_text: str,
    source_texts: list[str],
) -> list[dict]:
    """Extract factual claims from answer, score each against source texts via lexical overlap."""

    sentences = _split_sentences(answer_text)
    claim_results = []

    for sent in sentences:
        if not _is_factual_claim(sent):
            continue

        best_score = max(
            (_score_lexical_overlap(sent, src) for src in source_texts),
            default=0.0,
        )

        if best_score >= 0.65:
            status = "verified"
        elif best_score >= 0.40:
            status = "plausible"
        else:
            status = "unverifiable"

        claim_results.append({
            "text": sent,
            "score": round(best_score, 4),
            "status": status,
        })

    return claim_results


def compute_verification_summary(claim_results: list[dict]) -> dict:
    if not claim_results:
        return {"verified": 0, "plausible": 0, "unverifiable": 0, "total": 0, "avg_score": 0.0}

    verified = sum(1 for c in claim_results if c["status"] == "verified")
    plausible = sum(1 for c in claim_results if c["status"] == "plausible")
    unverifiable = sum(1 for c in claim_results if c["status"] == "unverifiable")
    scores = [c["score"] for c in claim_results]

    return {
        "verified": verified,
        "plausible": plausible,
        "unverifiable": unverifiable,
        "total": len(claim_results),
        "avg_score": round(sum(scores) / len(scores), 4),
    }


def should_abstain(
    claim_results: list[dict],
    min_avg_score: float = 0.50,
    max_unverifiable_ratio: float = 0.50,
) -> tuple[bool, str | None]:
    if not claim_results:
        return False, None

    scores = [c["score"] for c in claim_results]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    unverifiable_count = sum(1 for c in claim_results if c["status"] == "unverifiable")
    unverifiable_ratio = unverifiable_count / len(claim_results) if claim_results else 0.0

    if avg_score < min_avg_score or unverifiable_ratio > max_unverifiable_ratio:
        return True, (
            f"I found relevant documents but could not reliably verify the answer.\n\n"
            f"Average evidence confidence: {avg_score:.0%} "
            f"(minimum: {min_avg_score:.0%}). "
            f"{unverifiable_count} of {len(claim_results)} claims could not be "
            f"confirmed against the source documents.\n\n"
            f"Here are the source documents for direct review:"
        )

    return False, None


def format_verification_notice(summary: dict) -> str | None:
    if summary["total"] == 0:
        return None
    parts = []
    if summary["verified"]:
        parts.append(f"{summary['verified']} verified")
    if summary["plausible"]:
        parts.append(f"{summary['plausible']} plausible")
    if summary["unverifiable"]:
        parts.append(f"{summary['unverifiable']} removed (unverifiable)")
    return " | ".join(parts) if parts else None

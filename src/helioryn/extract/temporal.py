# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import re
from datetime import datetime

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DATE_PATTERNS = [
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),              # 2024-01-15
    re.compile(r"(\w+)\s+(\d{1,2}),?\s*(\d{4})"),            # January 15, 2024
    re.compile(r"(\d{1,2})\s+(\w+)\s+(\d{4})"),              # 15 January 2024
    re.compile(r"(\w+)\s+(\d{4})"),                           # January 2024
    re.compile(r"Q[1-4]\s+(\d{4})", re.IGNORECASE),          # Q1 2024
    re.compile(r"(\d{4})"),                                   # 2024 (standalone year)
]

RELATIVE_PATTERNS = [
    re.compile(r"(?:last|this|next)\s+(?:week|month|quarter|year)", re.IGNORECASE),
    re.compile(r"(?:yesterday|today|tomorrow)", re.IGNORECASE),
    re.compile(r"\d+\s+(?:day|week|month|year)s?\s+ago", re.IGNORECASE),
]


def extract_temporal_references(text: str) -> list[dict]:
    references: list[dict] = []
    seen: set[str] = set()
    covered: list[tuple[int, int]] = []

    def _is_covered(start: int, end: int) -> bool:
        return any(s <= start and end <= e for s, e in covered)

    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if _is_covered(start, end):
                continue
            raw = match.group(0).strip()
            ref = _parse_date_match(match, pattern)
            if ref:
                if raw.lower() in seen:
                    covered.append((start, end))
                    continue
                seen.add(raw.lower())
                covered.append((start, end))
                references.append(ref)

    for pattern in RELATIVE_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if _is_covered(start, end):
                continue
            raw = match.group(0).strip()
            if raw.lower() in seen:
                continue
            seen.add(raw.lower())
            references.append({
                "text": raw,
                "type": "relative",
                "normalized": None,
            })
            covered.append((start, end))

    return references


def _parse_date_match(match: re.Match, pattern: re.Pattern) -> dict | None:
    raw = match.group(0)
    groups = match.groups()

    if pattern == DATE_PATTERNS[0]:  # 2024-01-15
        return {"text": raw, "type": "absolute", "normalized": f"{groups[0]}-{int(groups[1]):02d}-{int(groups[2]):02d}"}

    elif pattern == DATE_PATTERNS[1]:  # January 15, 2024
        month = MONTH_NAMES.get(groups[0].lower())
        if month:
            return {"text": raw, "type": "absolute", "normalized": f"{groups[2]}-{month:02d}-{int(groups[1]):02d}"}

    elif pattern == DATE_PATTERNS[2]:  # 15 January 2024
        month = MONTH_NAMES.get(groups[1].lower())
        if month:
            return {"text": raw, "type": "absolute", "normalized": f"{groups[2]}-{month:02d}-{int(groups[0]):02d}"}

    elif pattern == DATE_PATTERNS[3]:  # January 2024
        month = MONTH_NAMES.get(groups[0].lower())
        if month:
            return {"text": raw, "type": "absolute", "normalized": f"{groups[1]}-{month:02d}"}

    elif pattern == DATE_PATTERNS[4]:  # Q1 2024
        return {"text": raw, "type": "quarter", "normalized": groups[0]}

    elif pattern == DATE_PATTERNS[5]:  # 2024 (year only)
        year = int(groups[0])
        if 1900 <= year <= 2100:
            return {"text": raw, "type": "year", "normalized": groups[0]}

    return None

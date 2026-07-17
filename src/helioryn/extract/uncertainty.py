# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import re

UNCERTAINTY_SIGNALS: dict[str, list[str]] = {
    "modal": ["may", "might", "could", "would", "can"],
    "hedging": ["suggests", "indicates", "appears", "seems", "reportedly", "allegedly"],
    "future": ["will", "planned", "expected", "proposed", "upcoming"],
    "attribution": ["according to", "cited", "quoted", "referenced"],
    "quantifier": ["approximately", "roughly", "about", "nearly", "around"],
}


def detect_uncertainty(text: str) -> dict:
    text_lower = text.lower()
    signals: dict[str, list[str]] = {}

    for category, terms in UNCERTAINTY_SIGNALS.items():
        found = []
        for t in terms:
            if " " in t:
                if t in text_lower:
                    found.append(t)
            else:
                if re.search(rf'\b{re.escape(t)}\b', text_lower):
                    found.append(t)
        if found:
            signals[category] = found

    score = _compute_uncertainty_score(signals)
    return {"score": score, "signals": signals}


def _compute_uncertainty_score(signals: dict[str, list[str]]) -> float:
    weights = {
        "modal": 0.4,
        "hedging": 0.6,
        "future": 0.3,
        "attribution": 0.2,
        "quantifier": 0.3,
    }
    max_possible = sum(weights.values())
    actual = 0.0
    for category, found in signals.items():
        if found:
            actual += weights.get(category, 0.2)
    return round(min(actual / max_possible, 1.0), 3)

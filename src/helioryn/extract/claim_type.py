# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import re

_OPINION_MARKERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(believe|believes|belief|think|thinks|thought)\b", re.I), "opinion"),
    (re.compile(r"\b(should|ought|could|might|perhaps|maybe|arguably|supposedly)\b", re.I), "opinion"),
    (re.compile(r"\b(in my opinion|in my view|it seems|it appears|arguably|conceivably)\b", re.I), "opinion"),
    (re.compile(r"\b(hopefully|unfortunately|regrettably|surprisingly|remarkably)\b", re.I), "opinion"),
    (re.compile(r"\b(best|worst|greatest|terrible|excellent|poor|mediocre)\b", re.I), "opinion"),
]

_PREDICTION_MARKERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(will|would|could|shall)\s+(be|become|see|reach|hit|grow|decline|rise|fall|increase|decrease)\b", re.I), "prediction"),
    (re.compile(r"\b(expected|forecast|projected|predicted|anticipated|estimated)\s+to\b", re.I), "prediction"),
    (re.compile(r"\b(forecast|outlook|projection|prediction|scenario)\b", re.I), "prediction"),
    (re.compile(r"\b(by\s+\d{4}|within\s+\d+\s+years|over\s+the\s+next)\b", re.I), "prediction"),
    (re.compile(r"\b(plan[s]?\s+to|aim[s]?\s+to|target[s]?\s+to|goal\s+of)\b", re.I), "prediction"),
]

_REPORT_MARKERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(according\s+to|said|says|stated|stated\s+that|reported\s+that|announced\s+that)\b", re.I), "report"),
    (re.compile(r"\b(revealed|confirmed|disclosed|published|released|declared)\b", re.I), "report"),
    (re.compile(r"\b(the\s+study|the\s+report|the\s+analysis|the\s+survey|the\s+data)\b", re.I), "report"),
    (re.compile(r"\b(cites?|citing|according\s+to|sources?\s+say|sources?\s+said)\b", re.I), "report"),
    (re.compile(r"\b(found\s+that|shows?\s+that|suggests?\s+that|indicates?\s+that)\b", re.I), "report"),
]


def classify_claim(text: str) -> str:
    if not text or len(text) < 10:
        return "fact"

    text_lower = text.lower()

    for pattern, label in _OPINION_MARKERS:
        if pattern.search(text):
            return "opinion"

    for pattern, label in _PREDICTION_MARKERS:
        if pattern.search(text):
            return "prediction"

    report_score = 0
    for pattern, label in _REPORT_MARKERS:
        if pattern.search(text):
            report_score += 1

    if report_score >= 2:
        return "report"
    if report_score == 1 and len(text) > 60:
        return "report"

    return "fact"

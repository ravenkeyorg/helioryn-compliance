# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import re
from uuid import UUID

import spacy

from helioryn.models import Claim, Entity, SourceSnapshot


_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.blank("en")
        _nlp.add_pipe("sentencizer")
    return _nlp


_BOILERPLATE_SENTENCE: list[re.Pattern] = [
    re.compile(r"^(?:sign\s+(?:in|up|out)|subscribe|click\s+here|read\s+more|learn\s+more|contact\s+us|privacy\s+policy|terms\s+of\s+service|cookie|share\s+this|follow\s+us|join\s+the\s+conversation)", re.I),
    re.compile(r"visit\s+(?:the|our)\s", re.I),
    re.compile(r"^\d+\s+words?$"),
    re.compile(r"^\w+\s+\^\s+\w+"),
]


def _is_quality_claim(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 60:
        return False
    if len(stripped) > 5000:
        return False
    for pat in _BOILERPLATE_SENTENCE:
        if pat.search(stripped):
            return False
    return True


def split_sentences(text: str) -> list[str]:
    nlp = _get_nlp()
    doc = nlp(text)
    return [s.text.strip() for s in doc.sents if len(s.text.strip()) > 20]


def extract_claims(source: SourceSnapshot) -> list[Claim]:
    from helioryn.extract.temporal import extract_temporal_references
    from helioryn.extract.uncertainty import detect_uncertainty as detect_claim_uncertainty
    from helioryn.extract.claim_type import classify_claim

    sentences = split_sentences(source.raw_text)
    claims = []
    for i, sent in enumerate(sentences):
        if not _is_quality_claim(sent):
            continue
        context = sentences[i - 1] if i > 0 else None

        temporal_refs = extract_temporal_references(sent)
        uncertainty = detect_claim_uncertainty(sent)
        claim_type = classify_claim(sent)

        claims.append(
            Claim(
                source_id=source.source_id,
                source_url=source.source_url,
                canonical_text=sent,
                original_text=sent,
                extraction_confidence=0.7,
                claim_type=claim_type,
                context_sentence=context,
            )
        )
    return claims


def extract_entities(text: str) -> list[dict]:
    from helioryn.extract.ner import extract_entities as ner_extract
    return ner_extract(text)

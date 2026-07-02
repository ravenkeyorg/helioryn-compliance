# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
import re

import spacy

from helioryn.models import Entity

# Map spaCy NER labels to Helioryn entity types
SPACY_LABEL_MAP = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "location",
    "LOC": "location",
    "EVENT": "event",
    "NORP": "concept",
    "FAC": "location",
    "PRODUCT": "concept",
    "WORK_OF_ART": "concept",
    "LAW": "concept",
    "DATE": "concept",
    "TIME": "concept",
    "MONEY": "concept",
    "QUANTITY": "concept",
    "PERCENT": "concept",
    "CARDINAL": "concept",
    "ORDINAL": "concept",
}

# Fallback regex for capitalized multi-word terms spaCy might miss
FALLBACK_ENTITY = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\b")

_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            import subprocess
            subprocess.run(
                ["python3", "-m", "spacy", "download", "en_core_web_sm"],
                check=True,
            )
            _nlp = spacy.load("en_core_web_sm")
    return _nlp


def extract_entities(text: str) -> list[dict]:
    nlp = _get_nlp()
    doc = nlp(text)

    seen: set[str] = set()
    entities = []

    # Primary: spaCy NER
    for ent in doc.ents:
        name = ent.text.strip()
        if not name or len(name) < 3:
            continue
        clean = name.removeprefix("The ").removeprefix("A ")
        if clean.lower() in seen or clean in seen:
            continue
        seen.add(clean.lower())
        seen.add(clean)

        etype = SPACY_LABEL_MAP.get(ent.label_, "concept")
        entities.append({
            "name": clean,
            "type": etype,
            "mention": name,
        })

    # Fallback: regex for multi-word capitalized terms spaCy might miss
    for match in FALLBACK_ENTITY.finditer(text):
        name = match.group(1)
        clean = name.removeprefix("The ").removeprefix("A ")
        if clean.lower() in seen or clean in seen:
            continue
        if len(clean.split()) < 2:
            continue
        seen.add(clean.lower())
        seen.add(clean)
        entities.append({
            "name": clean,
            "type": "concept",
            "mention": name,
        })

    return entities

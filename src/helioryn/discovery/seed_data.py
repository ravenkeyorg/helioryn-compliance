# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


def load_entities(filepath: str) -> list[dict]:
    with open(filepath) as f:
        return json.load(f)


def load_govs() -> list[dict]:
    return load_entities(str(DATA_DIR / "governments.json"))


def load_companies() -> list[dict]:
    return load_entities(str(DATA_DIR / "companies.json"))


def load_researchers() -> list[dict]:
    return load_entities(str(DATA_DIR / "researchers.json"))


def load_investors() -> list[dict]:
    return load_entities(str(DATA_DIR / "investors.json"))


def _default_govs() -> list[dict]:
    return [
        {"name": "United States", "level": "country", "search_name": "United States", "country": "US", "region": "North America"},
        {"name": "United Kingdom", "level": "country", "search_name": "United Kingdom", "country": "GB", "region": "Europe"},
        {"name": "Canada", "level": "country", "search_name": "Canada", "country": "CA", "region": "North America"},
        {"name": "Australia", "level": "country", "search_name": "Australia", "country": "AU", "region": "Oceania"},
        {"name": "Germany", "level": "country", "search_name": "Germany", "country": "DE", "region": "Europe"},
        {"name": "France", "level": "country", "search_name": "France", "country": "FR", "region": "Europe"},
        {"name": "Japan", "level": "country", "search_name": "Japan", "country": "JP", "region": "Asia"},
        {"name": "China", "level": "country", "search_name": "China", "country": "CN", "region": "Asia"},
        {"name": "India", "level": "country", "search_name": "India", "country": "IN", "region": "Asia"},
        {"name": "Russia", "level": "country", "search_name": "Russia", "country": "RU", "region": "Europe/Asia"},
        {"name": "Brazil", "level": "country", "search_name": "Brazil", "country": "BR", "region": "South America"},
        {"name": "South Korea", "level": "country", "search_name": "South Korea", "country": "KR", "region": "Asia"},
        {"name": "European Union", "level": "international", "search_name": "European Union", "country": "EU", "region": "Europe"},
        {"name": "United Nations", "level": "international", "search_name": "United Nations", "country": "UN", "region": "Global"},
        {"name": "NATO", "level": "international", "search_name": "NATO", "country": "NATO", "region": "Europe/North America"},
        {"name": "OECD", "level": "international", "search_name": "OECD", "country": "OECD", "region": "Global"},
        {"name": "World Bank", "level": "international", "search_name": "World Bank", "country": "WB", "region": "Global"},
        {"name": "International Monetary Fund", "level": "international", "search_name": "IMF", "country": "IMF", "region": "Global"},
        {"name": "California", "level": "state", "search_name": "California", "country": "US", "region": "US-West"},
        {"name": "New York", "level": "state", "search_name": "New York", "country": "US", "region": "US-East"},
        {"name": "Texas", "level": "state", "search_name": "Texas", "country": "US", "region": "US-South"},
        {"name": "Florida", "level": "state", "search_name": "Florida", "country": "US", "region": "US-South"},
        {"name": "Illinois", "level": "state", "search_name": "Illinois", "country": "US", "region": "US-Midwest"},
        {"name": "Washington", "level": "state", "search_name": "Washington", "country": "US", "region": "US-West"},
        {"name": "Massachusetts", "level": "state", "search_name": "Massachusetts", "country": "US", "region": "US-East"},
        {"name": "Tokyo", "level": "city", "search_name": "Tokyo", "country": "JP", "region": "Asia"},
        {"name": "London", "level": "city", "search_name": "London", "country": "GB", "region": "Europe"},
        {"name": "New York City", "level": "city", "search_name": "New York City", "country": "US", "region": "US-East"},
        {"name": "Beijing", "level": "city", "search_name": "Beijing", "country": "CN", "region": "Asia"},
        {"name": "Paris", "level": "city", "search_name": "Paris", "country": "FR", "region": "Europe"},
        {"name": "Berlin", "level": "city", "search_name": "Berlin", "country": "DE", "region": "Europe"},
        {"name": "Moscow", "level": "city", "search_name": "Moscow", "country": "RU", "region": "Europe/Asia"},
        {"name": "Seoul", "level": "city", "search_name": "Seoul", "country": "KR", "region": "Asia"},
        {"name": "Singapore", "level": "city", "search_name": "Singapore", "country": "SG", "region": "Asia"},
        {"name": "San Francisco", "level": "city", "search_name": "San Francisco", "country": "US", "region": "US-West"},
        {"name": "Seattle", "level": "city", "search_name": "Seattle", "country": "US", "region": "US-West"},
        {"name": "Federal Trade Commission", "level": "agency", "search_name": "FTC", "country": "US", "region": "US"},
        {"name": "Federal Communications Commission", "level": "agency", "search_name": "FCC", "country": "US", "region": "US"},
        {"name": "Department of Defense", "level": "agency", "search_name": "US Department of Defense", "country": "US", "region": "US"},
        {"name": "Department of Energy", "level": "agency", "search_name": "US Department of Energy", "country": "US", "region": "US"},
        {"name": "European Commission", "level": "agency", "search_name": "European Commission", "country": "EU", "region": "Europe"},
        {"name": "UK Competition and Markets Authority", "level": "agency", "search_name": "CMA United Kingdom", "country": "GB", "region": "Europe"},
    ]


def generate_seed_json(output_path: str):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    data = _default_govs()
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} entities to {output_path}")
    return len(data)

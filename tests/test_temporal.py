# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
"""Tests for temporal reference extraction."""

from helioryn.extract.temporal import extract_temporal_references


class TestExtractTemporalReferences:
    def test_iso_date(self):
        refs = extract_temporal_references("Published on 2024-01-15")
        assert len(refs) == 1
        assert refs[0]["type"] == "absolute"
        assert refs[0]["normalized"] == "2024-01-15"

    def test_month_day_year(self):
        refs = extract_temporal_references("On January 15, 2024, the report was released")
        assert len(refs) == 1
        assert refs[0]["normalized"] == "2024-01-15"

    def test_day_month_year(self):
        refs = extract_temporal_references("Published 15 January 2024")
        assert len(refs) == 1
        assert refs[0]["normalized"] == "2024-01-15"

    def test_month_year(self):
        refs = extract_temporal_references("In January 2024, the policy was enacted")
        assert len(refs) == 1
        assert refs[0]["normalized"] == "2024-01"

    def test_quarter(self):
        refs = extract_temporal_references("Q1 2024 saw major developments")
        assert len(refs) == 1
        assert refs[0]["type"] == "quarter"

    def test_year_only(self):
        refs = extract_temporal_references("The 2024 AI Act was passed")
        assert len(refs) == 1
        assert refs[0]["type"] == "year"
        assert refs[0]["normalized"] == "2024"

    def test_year_out_of_range(self):
        refs = extract_temporal_references("In 999 BC, the event occurred")
        assert len(refs) == 0

    def test_relative_patterns(self):
        refs = extract_temporal_references("Last week the decision was made")
        assert len(refs) == 1
        assert refs[0]["type"] == "relative"

    def test_multiple_dates(self):
        refs = extract_temporal_references("From January 2024 to March 2024")
        assert len(refs) == 2

    def test_dedup_same_date(self):
        refs = extract_temporal_references("2024-01-15 and again on 2024-01-15")
        assert len(refs) == 1

    def test_no_dates(self):
        refs = extract_temporal_references("The sky is blue and grass is green.")
        assert refs == []

    def test_sep_parses_as_september(self):
        """Regression: 'sep' should map to September (9), not July (7)."""
        refs = extract_temporal_references("Published Sep 2024")
        assert len(refs) == 1
        n = refs[0]["normalized"]
        assert n == "2024-09", f"Expected 2024-09, got {n}"

    def test_all_months_by_name(self):
        """All 12 full month names should parse correctly."""
        for month in ["January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]:
            refs = extract_temporal_references(f"In {month} 2024")
            assert len(refs) == 1, f"{month} 2024 failed to parse"

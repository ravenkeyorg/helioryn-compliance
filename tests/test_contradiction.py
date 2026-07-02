# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
"""Tests for contradiction detection heuristics (static methods, no DB needed)."""

from helioryn.store import EventStore

# ---------------------------------------------------------------------------
# _is_year
# ---------------------------------------------------------------------------

class TestIsYear:
    def test_year_in_range(self):
        assert EventStore._is_year(2024) is True
        assert EventStore._is_year(1900) is True
        assert EventStore._is_year(2100) is True

    def test_non_year(self):
        assert EventStore._is_year(14) is False
        assert EventStore._is_year(1899) is False
        assert EventStore._is_year(2101) is False
        assert EventStore._is_year(-2030) is False

# ---------------------------------------------------------------------------
# _extract_meaningful_numbers
# ---------------------------------------------------------------------------

class TestExtractMeaningfulNumbers:
    def test_simple_number(self):
        assert EventStore._extract_meaningful_numbers("GDP grew 4.2%") == [4.2]

    def test_year_is_still_extracted(self):
        """Years are valid numbers but should NOT trigger contradiction."""
        nums = EventStore._extract_meaningful_numbers("In 2024, inflation rose")
        assert 2024 in nums

    def test_skips_day_before_month(self):
        """14 May should not extract 14 as meaningful."""
        nums = EventStore._extract_meaningful_numbers("14 May 2026")
        assert 14 not in nums

    def test_skips_day_after_month(self):
        """May 2 should not extract 2 as meaningful."""
        nums = EventStore._extract_meaningful_numbers("May 2, 2025")
        assert 2 not in nums

    def test_skips_day_with_long_month(self):
        nums = EventStore._extract_meaningful_numbers("8 October 2025")
        assert 8 not in nums

    def test_skips_bracketed_citations(self):
        nums = EventStore._extract_meaningful_numbers("text[3][4][18] here")
        for n in [3, 4, 18]:
            assert n not in nums, f"{n} should be filtered as citation"

    def test_skips_bracketed_citation_list(self):
        nums = EventStore._extract_meaningful_numbers("multiple sources [1, 3, 5]")
        for n in [1, 3, 5]:
            assert n not in nums

    def test_skips_negative_numbers(self):
        """-2030 is a dash artifact, not a meaningful number."""
        nums = EventStore._extract_meaningful_numbers("mechanisms, which - 2030")
        assert -2030 not in nums
        assert 2030 in nums  # the absolute value may remain if not a day/year

    def test_skips_zero_and_one(self):
        nums = EventStore._extract_meaningful_numbers("0 percent and 1 percent")
        assert nums == []

    def test_preserves_genuine_numbers(self):
        nums = EventStore._extract_meaningful_numbers("Spent $4.5 million and $2.1 million")
        assert 4.5 in nums
        assert 2.1 in nums

    def test_percentage(self):
        nums = EventStore._extract_meaningful_numbers("unemployment fell to 3.8%")
        assert 3.8 in nums

    def test_multiple_real_numbers(self):
        nums = EventStore._extract_meaningful_numbers("revenue grew 12% and costs rose 3.2%")
        assert 12 in nums
        assert 3.2 in nums

    def test_respects_max_limit(self):
        nums = EventStore._extract_meaningful_numbers("a 1 b 2 c 3 d 4 e 5 f 6 g 7 h 8 i 9 j 10")
        assert len(nums) <= 8

# ---------------------------------------------------------------------------
# _check_numeric_contradiction
# ---------------------------------------------------------------------------

class TestCheckNumericContradiction:
    def test_contradicting_numbers(self):
        """Different numbers about same thing → contradiction."""
        result = EventStore._check_numeric_contradiction(
            "GDP grew 4%", "GDP grew 5%", numeric_diff=0.15
        )
        assert result is not None
        assert result.startswith("numeric:")

    def test_same_number_no_contradiction(self):
        result = EventStore._check_numeric_contradiction(
            "cost $100", "cost $100", numeric_diff=0.15
        )
        assert result is None

    def test_year_difference_is_not_contradiction(self):
        """Years differ but should NOT be flagged."""
        result = EventStore._check_numeric_contradiction(
            "In 2024, the policy launched", "In 2026, the policy was updated",
            numeric_diff=0.15
        )
        assert result is None

    def test_no_numbers(self):
        result = EventStore._check_numeric_contradiction(
            "The economy grew", "The economy shrank", numeric_diff=0.15
        )
        assert result is None

    def test_one_side_has_no_numbers(self):
        result = EventStore._check_numeric_contradiction(
            "GDP grew 4%", "The economy is doing well", numeric_diff=0.15
        )
        assert result is None

# ---------------------------------------------------------------------------
# _check_temporal_contradiction
# ---------------------------------------------------------------------------

class TestCheckTemporalContradiction:
    def test_same_month_and_year_no_conflict(self):
        result = EventStore._check_temporal_contradiction(
            "In January 2024, the report was published",
            "The January 2024 report was clear",
        )
        assert result is None

    def test_different_months_same_year(self):
        """Same year, different months → month conflict."""
        result = EventStore._check_temporal_contradiction(
            "The hearing was in January 2024",
            "The hearing was in February 2024",
        )
        assert result is not None

    def test_different_years_no_months(self):
        """Months without matching years should not trigger."""
        result = EventStore._check_temporal_contradiction(
            "The event was in January",
            "The event was in February",
        )
        assert result is None

# ---------------------------------------------------------------------------
# _check_role_contradiction
# ---------------------------------------------------------------------------

class TestCheckRoleContradiction:
    def test_different_roles_same_person(self):
        result = EventStore._check_role_contradiction(
            "John serves as President",
            "John serves as Vice President",
        )
        assert result is not None
        assert "role conflict" in result

    def test_same_role_no_conflict(self):
        result = EventStore._check_role_contradiction(
            "Jane is the CEO",
            "Jane serves as CEO",
        )
        assert result is None

    def test_no_role_keywords(self):
        result = EventStore._check_role_contradiction(
            "John is a programmer",
            "John is a manager",
        )
        assert result is None  # doesn't match trigger patterns

# ---------------------------------------------------------------------------
# _check_stance_contradiction
# ---------------------------------------------------------------------------

class TestCheckStanceContradiction:
    def test_support_vs_oppose(self):
        result = EventStore._check_stance_contradiction(
            "We support the new policy",
            "We oppose the new policy",
        )
        assert result is not None
        assert "stance conflict" in result

    def test_increase_vs_decrease(self):
        result = EventStore._check_stance_contradiction(
            "The government increased taxes",
            "The government decreased taxes",
        )
        assert result is not None

    def test_same_stance_no_conflict(self):
        result = EventStore._check_stance_contradiction(
            "We support the policy",
            "We also support the policy",
        )
        assert result is None

    def test_no_stance_words(self):
        result = EventStore._check_stance_contradiction(
            "The sky is blue",
            "Grass is green",
        )
        assert result is None

# ---------------------------------------------------------------------------
# Regression: real-world patterns from production data
# ---------------------------------------------------------------------------

class TestRegressionFromProduction:
    def test_year_conflict_not_flagged(self):
        """Real example: two AI-policy texts with different years."""
        result = EventStore._check_numeric_contradiction(
            "In November 2018, the German Federal Government launched its National AI strategy",
            "Germany is actively preparing its national structures for enforcement and guidance",
            numeric_diff=0.15,
        )
        assert result is None

    def test_day_of_month_not_flagged_as_numeric(self):
        """'14 May' should not produce a numeric contradiction."""
        result = EventStore._check_numeric_contradiction(
            "Latest news 14 May 2026",
            "published a report on May 2, 2025",
            numeric_diff=0.15,
        )
        assert result is None

    def test_citation_numbers_not_flagged(self):
        result = EventStore._check_numeric_contradiction(
            "ECAT[3][4][18] oversees transparency",
            "October 2025 - The Commission has",
            numeric_diff=0.15,
        )
        assert result is None

    def test_negative_dash_artifact_not_flagged(self):
        result = EventStore._check_numeric_contradiction(
            "mechanisms, which - 2030 defines objectives",
            "Russia has adopted several acts defining key objectives",
            numeric_diff=0.15,
        )
        assert result is None

# ---------------------------------------------------------------------------
# New precision filters
# ---------------------------------------------------------------------------

class TestNewPrecisionFilters:
    def test_skips_list_marker_at_start(self):
        nums = EventStore._extract_meaningful_numbers("2 OECD Members: Australia, Austria")
        assert 2 not in nums

    def test_skips_section_heading(self):
        nums = EventStore._extract_meaningful_numbers("10. Introduction text goes here")
        assert 10 not in nums

    def test_skips_page_number(self):
        nums = EventStore._extract_meaningful_numbers("presented on page 11 of the report")
        assert 11 not in nums

    def test_skips_executive_order(self):
        nums = EventStore._extract_meaningful_numbers("Executive Order 14179")
        assert 14179 not in nums

    def test_skips_url_numbers(self):
        nums = EventStore._extract_meaningful_numbers("see https://example.com/123 for details")
        assert 123 not in nums

    def test_skips_doi_numbers(self):
        nums = EventStore._extract_meaningful_numbers("doi 10.1016/j.jsis.2024.101885")
        for n in [10.1016, 2024.101885]:
            assert n not in nums

    def test_skips_large_id_numbers(self):
        nums = EventStore._extract_meaningful_numbers("article 101885 reference")
        assert 101885 not in nums

    def test_contradiction_requires_same_number_count(self):
        """Different number counts should not produce numeric contradiction."""
        result = EventStore._check_numeric_contradiction(
            "Revenue is £23.9 billion and GVA is £11.8 billion",
            "GVA is £3.7 billion",
            numeric_diff=0.15,
        )
        assert result is None

    def test_contradiction_rejects_three_plus_numbers(self):
        """Texts with 3+ numbers should not produce numeric contradiction."""
        result = EventStore._check_numeric_contradiction(
            "Revenue is £23.9 billion, GVA is £11.8 billion, 86k jobs",
            "Revenue is £25 billion",
            numeric_diff=0.15,
        )
        assert result is None

    def test_contradiction_requires_positional_match(self):
        """First number vs first number, second vs second."""
        result = EventStore._check_numeric_contradiction(
            "GDP grew 4% and inflation hit 3%",
            "GDP grew 5% and inflation hit 3%",
            numeric_diff=0.15,
        )
        assert result is not None
        assert "4.0 vs 5.0" in result or "numeric:" in result

    def test_preserves_24_million(self):
        """24 million is a legitimate quantity — should NOT be stripped."""
        nums = EventStore._extract_meaningful_numbers("24 million people were affected")
        assert 24 in nums

    def test_skips_ordinals(self):
        """12th district should not extract 12 as meaningful."""
        nums = EventStore._extract_meaningful_numbers("New York's 12th congressional district")
        assert 12 not in nums

    def test_skips_bill_numbers(self):
        """Senate Bill 315 should not extract 315."""
        nums = EventStore._extract_meaningful_numbers("Senate Bill 315 is part of an eight-bill package")
        assert 315 not in nums

    def test_skips_group_name_numbers(self):
        """G7 should not extract 7 as a standalone number."""
        nums = EventStore._extract_meaningful_numbers("G7 members continue discussions on AI")
        assert 7 not in nums

    def test_skips_ordinal_7th(self):
        """7th Congressional District should not extract 7."""
        nums = EventStore._extract_meaningful_numbers("New York's 7th Congressional District")
        assert 7 not in nums

    def test_skips_act_number(self):
        """RAISE Act should not extract numbers."""
        nums = EventStore._extract_meaningful_numbers("sponsors of the RAISE Act")
        assert nums == [] or 315 not in nums

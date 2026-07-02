# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
"""Tests for uncertainty detection."""

from helioryn.extract.uncertainty import detect_uncertainty, _compute_uncertainty_score


class TestDetectUncertainty:
    def test_modal_uncertainty(self):
        result = detect_uncertainty("The policy may reduce emissions")
        assert result["score"] > 0
        assert "modal" in result["signals"]

    def test_hedging(self):
        result = detect_uncertainty("The data suggests a correlation")
        assert result["score"] > 0
        assert "hedging" in result["signals"]

    def test_attribution(self):
        result = detect_uncertainty("According to the report, emissions rose")
        assert result["score"] > 0
        assert "attribution" in result["signals"]

    def test_quantifier(self):
        result = detect_uncertainty("Approximately 4.2 million people")
        assert result["score"] > 0
        assert "quantifier" in result["signals"]

    def test_no_uncertainty(self):
        result = detect_uncertainty("The sky is blue and grass is green.")
        assert result["score"] == 0
        assert result["signals"] == {}

    def test_multiple_categories(self):
        result = detect_uncertainty("The data may suggest approximately 4 million")
        cats = list(result["signals"].keys())
        assert len(cats) >= 2

    def test_can_not_match_cannot(self):
        """'can' in 'cannot' should NOT be detected as modal uncertainty."""
        result = detect_uncertainty("The government cannot ignore this issue")
        assert "modal" not in result["signals"] or "can" not in result["signals"]["modal"]

    def test_may_not_match_maybe(self):
        """'may' in 'maybe' should NOT be detected as modal uncertainty."""
        result = detect_uncertainty("Maybe the policy will change")
        assert "modal" not in result["signals"] or "may" not in result["signals"]["modal"]

    def test_plan_not_match_planned(self):
        """'plan' (noun) should NOT match the 'planned' signal term."""
        result = detect_uncertainty("We have a plan for the future")
        assert "future" not in result["signals"]

    def test_multi_word_attribution(self):
        result = detect_uncertainty("According to sources, the deal is done")
        assert "attribution" in result["signals"]

    def test_empty_text(self):
        result = detect_uncertainty("")
        assert result["score"] == 0
        assert result["signals"] == {}


class TestComputeUncertaintyScore:
    def test_score_in_range(self):
        score = _compute_uncertainty_score({"modal": ["may"]})
        assert 0 <= score <= 1

    def test_no_signals_zero(self):
        score = _compute_uncertainty_score({})
        assert score == 0

    def test_all_categories_max(self):
        score = _compute_uncertainty_score({
            "modal": ["may"], "hedging": ["suggests"],
            "future": ["will"], "attribution": ["according to"],
            "quantifier": ["approximately"],
        })
        assert score > 0.9

    def test_partial_score(self):
        score = _compute_uncertainty_score({"modal": ["may"], "hedging": ["suggests"]})
        assert 0.3 < score < 0.7

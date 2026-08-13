"""Tests for backend/valuation/risk_dashboard.py (four rule-based
categories) and backend/ai/tasks/risk_explanation.py (two AI-assisted
categories, constrained to LOW/MEDIUM/HIGH even against adversarial output)."""

from __future__ import annotations

import pytest

from backend.ai.testing import FakeAIAdapter
from backend.ai.tasks.risk_explanation import score_strategic_and_execution_risk
from backend.valuation.risk_dashboard import (
    build_risk_dashboard,
    score_data_risk,
    score_forecast_risk,
    score_market_risk,
    score_model_risk,
)


# --------------------------------------------------------------------------
# Rule-based categories
# --------------------------------------------------------------------------


@pytest.mark.parametrize("coverage,expected", [(95.0, "LOW"), (85.0, "LOW"), (70.0, "MEDIUM"), (60.0, "MEDIUM"), (40.0, "HIGH")])
def test_data_risk_thresholds(coverage, expected):
    assert score_data_risk(coverage).score == expected


@pytest.mark.parametrize(
    "methodologies_used,spread,expected",
    [
        (0, 5.0, "HIGH"),  # zero methodologies always HIGH
        (2, 10.0, "LOW"),
        (2, 45.0, "HIGH"),  # spread > 40 forces HIGH
        (1, 10.0, "MEDIUM"),
        (2, 30.0, "MEDIUM"),
    ],
)
def test_model_risk_thresholds(methodologies_used, spread, expected):
    assert score_model_risk(methodologies_used, spread).score == expected


@pytest.mark.parametrize(
    "classification,expected",
    [("CONSERVATIVE", "LOW"), ("REASONABLE", "LOW"), ("AGGRESSIVE", "MEDIUM"), ("EXTREME", "HIGH")],
)
def test_forecast_risk_thresholds(classification, expected):
    assert score_forecast_risk(classification).score == expected


@pytest.mark.parametrize(
    "beta,vol,expected",
    [
        (1.0, 20.0, "LOW"),
        (1.4, 45.0, "MEDIUM"),
        (2.0, 60.0, "HIGH"),
        (None, None, "HIGH"),  # absence of data treated as HIGH, not benign
    ],
)
def test_market_risk_thresholds(beta, vol, expected):
    assert score_market_risk(beta, vol).score == expected


# --------------------------------------------------------------------------
# AI-assisted categories -- constrained to enum even with adversarial output
# --------------------------------------------------------------------------


def test_ai_strategic_execution_scores_valid_enum():
    fake = FakeAIAdapter(
        fixed_text='{"strategic_risk": {"score": "HIGH", "explanation": "Conglomerate discount persistent."}, '
        '"execution_risk": {"score": "MEDIUM", "explanation": "Multiple segments to separate."}}'
    )
    result = score_strategic_and_execution_risk(fake, {"conglomerate_discount_pct": -20})
    assert result.abstained is False
    assert result.strategic_risk.score == "HIGH"
    assert result.strategic_risk.ai_assisted is True
    assert result.execution_risk.score == "MEDIUM"
    assert result.execution_risk.ai_assisted is True


def test_ai_adversarial_free_text_score_is_rejected():
    # Adversarial: AI ignores instructions and returns a free-text score
    # instead of LOW/MEDIUM/HIGH -- must be discarded (ABSTAIN), not coerced.
    fake = FakeAIAdapter(
        fixed_text='{"strategic_risk": {"score": "very risky honestly", "explanation": "..."}, '
        '"execution_risk": {"score": "MEDIUM", "explanation": "..."}}'
    )
    result = score_strategic_and_execution_risk(fake, {"conglomerate_discount_pct": -20})
    assert result.abstained is True
    assert result.strategic_risk is None
    assert result.execution_risk is None


def test_ai_adversarial_extra_enum_value_rejected():
    # Adversarial: AI invents a plausible-sounding but unauthorized level.
    fake = FakeAIAdapter(
        fixed_text='{"strategic_risk": {"score": "CRITICAL", "explanation": "..."}, '
        '"execution_risk": {"score": "LOW", "explanation": "..."}}'
    )
    result = score_strategic_and_execution_risk(fake, {"conglomerate_discount_pct": -20})
    assert result.abstained is True


# --------------------------------------------------------------------------
# Full dashboard composition
# --------------------------------------------------------------------------


def test_build_risk_dashboard_composes_all_six():
    fake = FakeAIAdapter(
        fixed_text='{"strategic_risk": {"score": "LOW", "explanation": "..."}, '
        '"execution_risk": {"score": "LOW", "explanation": "..."}}'
    )
    ai_result = score_strategic_and_execution_risk(fake, {"context": "ok"})
    assert not ai_result.abstained

    dashboard = build_risk_dashboard(
        coverage_pct=90.0,
        methodologies_used=2,
        sensitivity_spread_pct=10.0,
        forecast_classification="REASONABLE",
        beta=1.0,
        volatility_pct=20.0,
        strategic_risk=ai_result.strategic_risk,
        execution_risk=ai_result.execution_risk,
    )
    assert len(dashboard.categories) == 6
    assert dashboard.data_risk.score == "LOW"
    assert dashboard.model_risk.score == "LOW"
    assert dashboard.forecast_risk.score == "LOW"
    assert dashboard.market_risk.score == "LOW"
    assert dashboard.strategic_risk.score == "LOW"
    assert dashboard.execution_risk.score == "LOW"


def test_build_risk_dashboard_rejects_non_ai_assisted_categories():
    from backend.schemas.valuation import RiskCategoryScore

    bad_strategic = RiskCategoryScore(category="Strategic Risk", score="LOW", explanation="not ai assisted", ai_assisted=False)
    good_execution = RiskCategoryScore(category="Execution Risk", score="LOW", explanation="ok", ai_assisted=True)
    with pytest.raises(ValueError):
        build_risk_dashboard(
            coverage_pct=90.0,
            methodologies_used=2,
            sensitivity_spread_pct=10.0,
            forecast_classification="REASONABLE",
            beta=1.0,
            volatility_pct=20.0,
            strategic_risk=bad_strategic,
            execution_risk=good_execution,
        )

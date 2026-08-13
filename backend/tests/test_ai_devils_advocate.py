import json

from backend.ai.tasks.devils_advocate import RISK_CATEGORIES, run_devils_advocate
from backend.ai.testing import FakeAIAdapter

VALUATION_OUTPUT = {"equity_value": 3000000.0, "implied_price_per_share": 150.0, "wacc": 0.09}


def test_devils_advocate_success():
    fixed = json.dumps(
        {
            "risk_challenges": [
                {"category": "Data Risk", "challenge": "Segment coverage is thin."},
                {"category": "Model Risk", "challenge": "WACC of 0.09 may understate risk."},
            ],
            "strongest_reason_too_high": "Terminal growth may be optimistic.",
            "strongest_reason_too_low": "Margin expansion not captured.",
            "most_sensitive_assumption": "wacc",
            "weakest_data_input": "segment revenue",
            "challenged_fair_value_low": 130.0,
            "challenged_fair_value_high": 170.0,
        }
    )
    adapter = FakeAIAdapter(fixed_text=fixed)
    result = run_devils_advocate(adapter, VALUATION_OUTPUT)
    assert not result.abstained
    assert result.advisory_only is True
    assert len(result.risk_challenges) == 2
    assert all(c.category in RISK_CATEGORIES for c in result.risk_challenges)
    assert result.challenged_fair_value_low == 130.0
    # valuation output itself must be untouched
    assert VALUATION_OUTPUT["equity_value"] == 3000000.0


def test_devils_advocate_abstains_without_valuation():
    adapter = FakeAIAdapter(fixed_text="{}")
    result = run_devils_advocate(adapter, {})
    assert result.abstained
    assert adapter.calls == []


def test_devils_advocate_abstains_on_unrecognized_categories():
    fixed = json.dumps({"risk_challenges": [{"category": "Not A Real Category", "challenge": "x"}]})
    adapter = FakeAIAdapter(fixed_text=fixed)
    result = run_devils_advocate(adapter, VALUATION_OUTPUT)
    assert result.abstained

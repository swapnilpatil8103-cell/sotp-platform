import json

from backend.ai.tasks.assumption_recommendation import recommend_assumptions
from backend.ai.testing import FakeAIAdapter


FINANCIALS = {"revenue": 394328.0, "ebit_margin_hist": 0.30}


def test_assumption_recommendation_marks_proposals_as_requiring_approval():
    fixed = json.dumps(
        {
            "proposals": [
                {"assumption_key": "wacc", "recommended_value": 0.09, "rationale": "Peer-implied WACC.", "confidence": 0.6},
                {"assumption_key": "terminal_growth_rate", "recommended_value": 0.025, "rationale": "Long-run GDP growth.", "confidence": 0.5},
            ]
        }
    )
    adapter = FakeAIAdapter(fixed_text=fixed)
    result = recommend_assumptions(adapter, FINANCIALS, {"peer_wacc": 0.088})
    assert not result.abstained
    assert len(result.proposals) == 2
    for p in result.proposals:
        assert p.requires_human_approval is True
    keys = {p.assumption_key for p in result.proposals}
    assert keys == {"wacc", "terminal_growth_rate"}


def test_assumption_recommendation_drops_out_of_scope_keys():
    fixed = json.dumps(
        {
            "proposals": [
                {"assumption_key": "wacc", "recommended_value": 0.09, "rationale": "ok"},
                {"assumption_key": "made_up_key", "recommended_value": 42, "rationale": "n/a"},
            ]
        }
    )
    adapter = FakeAIAdapter(fixed_text=fixed)
    result = recommend_assumptions(adapter, FINANCIALS)
    assert not result.abstained
    assert len(result.proposals) == 1
    assert "made_up_key" in result.dropped_out_of_scope


def test_assumption_recommendation_abstains_without_financials():
    adapter = FakeAIAdapter(fixed_text="{}")
    result = recommend_assumptions(adapter, {})
    assert result.abstained
    assert adapter.calls == []


def test_assumption_recommendation_abstains_on_no_proposals():
    adapter = FakeAIAdapter(fixed_text=json.dumps({"proposals": []}))
    result = recommend_assumptions(adapter, FINANCIALS)
    assert result.abstained

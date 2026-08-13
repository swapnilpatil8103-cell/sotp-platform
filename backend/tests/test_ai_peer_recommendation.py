import json

from backend.ai.tasks.peer_recommendation import recommend_peers
from backend.ai.testing import FakeAIAdapter


def test_peer_recommendation_requires_approval_flag():
    fixed = json.dumps(
        {"candidates": [{"ticker": "msft", "rationale": "Large-cap software peer."}]}
    )
    adapter = FakeAIAdapter(fixed_text=fixed)
    result = recommend_peers(adapter, "AAPL", "Software", {"revenue": 394328.0})
    assert not result.abstained
    assert result.requires_human_approval is True
    assert result.candidates[0].ticker == "MSFT"


def test_peer_recommendation_abstains_without_business_category():
    adapter = FakeAIAdapter(fixed_text="{}")
    result = recommend_peers(adapter, "AAPL", "", {})
    assert result.abstained
    assert adapter.calls == []


def test_peer_recommendation_abstains_on_empty_candidates():
    adapter = FakeAIAdapter(fixed_text=json.dumps({"candidates": []}))
    result = recommend_peers(adapter, "AAPL", "Software", {})
    assert result.abstained

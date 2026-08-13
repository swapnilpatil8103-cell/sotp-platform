import json

from backend.ai.tasks.methodology_recommendation import recommend_methodology
from backend.ai.testing import FakeAIAdapter
from backend.data.business_classifier import classify_business


def test_methodology_recommendation_stays_within_eligible_list():
    classification = classify_business("7372")  # Software
    fixed = json.dumps(
        {
            "ranked": ["DCF", "EV/Revenue", "SOTP"],  # SOTP is NOT eligible for Software
            "rationale": {"DCF": "Strong FCF visibility.", "EV/Revenue": "Useful given growth stage.", "SOTP": "n/a"},
        }
    )
    adapter = FakeAIAdapter(fixed_text=fixed)
    result = recommend_methodology(
        adapter, classification.category, classification.eligible_methodologies, {"coverage": 90}
    )
    assert not result.abstained
    assert set(result.ranked_methodologies).issubset(set(classification.eligible_methodologies))
    assert "SOTP" in result.dropped_out_of_scope
    assert "SOTP" not in result.ranked_methodologies


def test_methodology_recommendation_abstains_with_no_eligible_methods():
    adapter = FakeAIAdapter(fixed_text="{}")
    result = recommend_methodology(adapter, "Other", [], {})
    assert result.abstained
    assert adapter.calls == []


def test_methodology_recommendation_abstains_on_unparseable_response():
    adapter = FakeAIAdapter(fixed_text="not json at all")
    result = recommend_methodology(adapter, "Software", ["DCF", "EV/Revenue", "EV/EBITDA"], {})
    assert result.abstained

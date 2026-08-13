from backend.ai.tasks.research_summary import generate_research_summary
from backend.ai.testing import FakeAIAdapter


FACTS = {"revenue": 394328.0, "net_income": 96995.0}
SEGMENTS = [{"name": "iPhone", "revenue": 200583.0}]


def test_research_summary_success():
    adapter = FakeAIAdapter(fixed_text="Revenue was 394328 with iPhone contributing 200583.")
    result = generate_research_summary(adapter, "AAPL", FACTS, SEGMENTS)
    assert not result.abstained
    assert result.validation_ok
    assert "394328" in result.summary_text
    # confirm prompt was built from real data
    assert "394328.0" in adapter.calls[0]
    assert "200583.0" in adapter.calls[0]


def test_research_summary_abstains_with_no_data():
    adapter = FakeAIAdapter(fixed_text="whatever")
    result = generate_research_summary(adapter, "AAPL", {}, [])
    assert result.abstained
    assert "Insufficient evidence" in result.reason
    assert adapter.calls == []  # never called the model with nothing to ground it


def test_research_summary_abstains_on_ai_unavailable():
    adapter = FakeAIAdapter(raise_unavailable=True)
    result = generate_research_summary(adapter, "AAPL", FACTS, SEGMENTS)
    assert result.abstained
    assert "AI unavailable" in result.reason


def test_research_summary_rejects_fabricated_numbers():
    adapter = FakeAIAdapter(fixed_text="Revenue was 394328 but next year will hit 999999999.")
    result = generate_research_summary(adapter, "AAPL", FACTS, SEGMENTS)
    assert result.abstained
    assert result.validation_ok is False
    assert "not traceable" in result.reason

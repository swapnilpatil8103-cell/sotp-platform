from backend.ai.tasks.memo_generator import generate_memo_section
from backend.ai.testing import FakeAIAdapter

MEMO_DATA = {"equity_value": 3000000.0, "implied_price_per_share": 150.0}


def test_memo_generator_success():
    adapter = FakeAIAdapter(fixed_text="The implied equity value is 3000000, or 150 per share.")
    result = generate_memo_section(adapter, "Valuation Summary", MEMO_DATA)
    assert not result.abstained
    assert result.validation_ok
    assert "3000000" in result.memo_text


def test_memo_generator_abstains_without_data():
    adapter = FakeAIAdapter(fixed_text="whatever")
    result = generate_memo_section(adapter, "Valuation Summary", {})
    assert result.abstained
    assert adapter.calls == []


def test_memo_generator_rejects_fabricated_numbers():
    adapter = FakeAIAdapter(fixed_text="The implied equity value is 3000000, but really it's 9999999999.")
    result = generate_memo_section(adapter, "Valuation Summary", MEMO_DATA)
    assert result.abstained
    assert result.validation_ok is False

from backend.ai.validation import validate_ai_numbers


def test_validation_passes_with_real_numbers():
    data = {"revenue": 394328.0, "segments": [{"name": "iPhone", "revenue": 200583.0}]}
    text = "Revenue was 394328 million, driven largely by iPhone revenue of 200583 million."
    result = validate_ai_numbers(text, data)
    assert result.ok
    assert result.unmatched_numbers == []


def test_validation_passes_with_rounded_numbers():
    data = {"revenue": 394328.12}
    text = "Revenue was approximately 394328.1 million."
    result = validate_ai_numbers(text, data)
    assert result.ok


def test_validation_catches_fabricated_number():
    data = {"revenue": 394328.0}
    text = "Revenue was 394328 million, and next year's revenue is projected at 999999 million."
    result = validate_ai_numbers(text, data)
    assert not result.ok
    assert 999999.0 in result.unmatched_numbers


def test_validation_ignores_years_and_small_numbers():
    data = {"revenue": 100.0}
    text = "In fiscal year 2024, the company reported across 3 segments."
    result = validate_ai_numbers(text, data)
    assert result.ok


def test_validation_percentage_fraction_equivalence():
    data = {"wacc": 0.085}
    text = "The proposed WACC is 8.5%."
    result = validate_ai_numbers(text, data)
    assert result.ok

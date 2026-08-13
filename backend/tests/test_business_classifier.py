"""Unit tests for backend.data.business_classifier -- verifies real, known
SIC codes map to the expected category and eligible methodologies."""

from __future__ import annotations

from backend.data.business_classifier import classify_business


def test_state_commercial_bank_sic_maps_to_bank():
    # SIC 6022 = State commercial banks
    result = classify_business(6022)
    assert result.category == "Bank"
    assert "DDM" in result.eligible_methodologies
    assert "P/B" in result.eligible_methodologies


def test_reit_sic_maps_to_reit():
    # SIC 6798 = Real Estate Investment Trusts
    result = classify_business("6798")
    assert result.category == "REIT"
    assert result.eligible_methodologies == ["NAV", "P/FFO", "AFFO"]


def test_crude_petroleum_sic_maps_to_energy():
    # SIC 1311 = Crude petroleum & natural gas
    result = classify_business(1311)
    assert result.category == "Energy"
    assert "EV/EBITDA" in result.eligible_methodologies
    assert "NAV" in result.eligible_methodologies


def test_prepackaged_software_sic_maps_to_software():
    # SIC 7372 = Prepackaged software
    result = classify_business(7372)
    assert result.category == "Software"
    assert "DCF" in result.eligible_methodologies
    assert "EV/Revenue" in result.eligible_methodologies


def test_missing_sic_defaults_to_other():
    result = classify_business(None)
    assert result.category == "Other"
    assert result.eligible_methodologies == ["DCF", "Comps"]


def test_unmatched_sic_defaults_to_other():
    result = classify_business(9999)
    assert result.category == "Other"


def test_non_numeric_sic_defaults_to_other_without_raising():
    result = classify_business("N/A")
    assert result.category == "Other"
    assert result.sic_code == "N/A"

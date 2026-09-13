"""Unit tests for backend.valuation.segment_valuation -- automated
per-segment valuation composing the existing DCF engine / multiple
arithmetic. Covers: multiple-method success, DCF-method success, and
missing-data-for-segment (must return a clear error, never a fabricated
value)."""

from __future__ import annotations

from backend.valuation.segment_valuation import (
    AutoValueSegmentsRequest,
    SegmentDcfAssumption,
    SegmentFacts,
    SegmentMultipleAssumption,
    SegmentValuationRequest,
    value_segment,
    value_segments,
)


def test_multiple_method_success():
    request = SegmentValuationRequest(
        segment=SegmentFacts(name="Cloud", revenue=1000.0, revenue_status="REPORTED"),
        methodology="multiple",
        multiple_assumption=SegmentMultipleAssumption(metric="ev_to_revenue", multiple_value=4.5),
    )
    result = value_segment(request)
    assert result.status == "SUGGESTED"
    assert result.error is None
    assert result.suggested_enterprise_value == 4500.0
    assert result.data_status == "SUGGESTED"
    assert result.requires_review is True


def test_multiple_method_ev_to_ebit():
    request = SegmentValuationRequest(
        segment=SegmentFacts(name="Hardware", ebit=200.0, ebit_status="REPORTED"),
        methodology="multiple",
        multiple_assumption=SegmentMultipleAssumption(metric="ev_to_ebit", multiple_value=10.0),
    )
    result = value_segment(request)
    assert result.status == "SUGGESTED"
    assert result.suggested_enterprise_value == 2000.0


def test_dcf_method_success_matches_run_dcf():
    from backend.schemas.valuation import DcfInput
    from backend.valuation.dcf import run_dcf

    dcf_assumption = SegmentDcfAssumption(
        revenue_growth_rates=[0.1, 0.08],
        ebit_margins=[0.2, 0.2],
        tax_rate=0.25,
        da_pct_of_revenue=[0.05, 0.05],
        capex_pct_of_revenue=[0.06, 0.06],
        nwc_change_pct_of_revenue=[0.01, 0.01],
        wacc=0.10,
        terminal_growth_rate=0.03,
    )
    request = SegmentValuationRequest(
        segment=SegmentFacts(name="Devices", revenue=1000.0, revenue_status="REPORTED"),
        methodology="dcf",
        dcf_assumption=dcf_assumption,
    )
    result = value_segment(request)
    assert result.status == "SUGGESTED"
    assert result.dcf_result is not None

    # Cross-check against calling run_dcf directly with the same segment-level inputs.
    direct = run_dcf(
        DcfInput(
            base_revenue=1000.0,
            revenue_growth_rates=[0.1, 0.08],
            ebit_margins=[0.2, 0.2],
            tax_rate=0.25,
            da_pct_of_revenue=[0.05, 0.05],
            capex_pct_of_revenue=[0.06, 0.06],
            nwc_change_pct_of_revenue=[0.01, 0.01],
            wacc=0.10,
            terminal_growth_rate=0.03,
            net_debt=0.0,
            cash_and_equivalents=0.0,
            investments=0.0,
            minority_interest=0.0,
            diluted_shares_outstanding=1.0,
        )
    )
    assert result.suggested_enterprise_value == direct.enterprise_value


def test_missing_data_for_multiple_method_returns_clear_error_not_fabrication():
    request = SegmentValuationRequest(
        segment=SegmentFacts(name="Other Bets", revenue=None, revenue_status="MISSING"),
        methodology="multiple",
        multiple_assumption=SegmentMultipleAssumption(metric="ev_to_revenue", multiple_value=4.5),
    )
    result = value_segment(request)
    assert result.status == "ERROR"
    assert result.suggested_enterprise_value is None
    assert result.error is not None
    assert "REPORTED" in result.error


def test_missing_data_for_dcf_method_returns_clear_error_not_fabrication():
    dcf_assumption = SegmentDcfAssumption(
        revenue_growth_rates=[0.1],
        ebit_margins=[0.2],
        tax_rate=0.25,
        da_pct_of_revenue=[0.05],
        capex_pct_of_revenue=[0.06],
        nwc_change_pct_of_revenue=[0.01],
        wacc=0.10,
        terminal_growth_rate=0.03,
    )
    request = SegmentValuationRequest(
        segment=SegmentFacts(name="Other Bets", revenue=None, revenue_status="MISSING"),
        methodology="dcf",
        dcf_assumption=dcf_assumption,
    )
    result = value_segment(request)
    assert result.status == "ERROR"
    assert result.suggested_enterprise_value is None
    assert result.error is not None


def test_missing_assumption_returns_error_not_default_method():
    request = SegmentValuationRequest(
        segment=SegmentFacts(name="Cloud", revenue=1000.0, revenue_status="REPORTED"),
        methodology="multiple",
        multiple_assumption=None,
    )
    result = value_segment(request)
    assert result.status == "ERROR"
    assert "multiple_assumption" in result.error


def test_value_segments_mixed_results():
    ok_request = SegmentValuationRequest(
        segment=SegmentFacts(name="Cloud", revenue=1000.0, revenue_status="REPORTED"),
        methodology="multiple",
        multiple_assumption=SegmentMultipleAssumption(metric="ev_to_revenue", multiple_value=4.5),
    )
    bad_request = SegmentValuationRequest(
        segment=SegmentFacts(name="Other Bets", revenue=None, revenue_status="MISSING"),
        methodology="multiple",
        multiple_assumption=SegmentMultipleAssumption(metric="ev_to_revenue", multiple_value=4.5),
    )
    response = value_segments(AutoValueSegmentsRequest(segments=[ok_request, bad_request]))
    assert len(response.suggestions) == 2
    assert response.suggestions[0].status == "SUGGESTED"
    assert response.suggestions[1].status == "ERROR"
    assert "SUGGESTED" in response.governance_note

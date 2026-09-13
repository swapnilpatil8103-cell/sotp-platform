"""Unit tests for backend.data.historical_analysis -- fixture-based, no network access."""

import pytest

from backend.data.historical_analysis import (
    YearValue,
    build_historical_trends,
    compute_cagr,
    compute_margin_trend,
    normalize_company_facts_multi_year,
    suggest_forward_growth,
)
from backend.models.enums import DataStatus


def _rev_units(entries):
    """entries: list of (val, fy, filed)

    `fy` here is the TRUE calendar fiscal year (these are calendar-year-end
    fixture companies), so `end`/`start` are derived directly from it. Real
    SEC XBRL data's own `fy` metadata field is NOT trustworthy for this (it
    reflects which filing's comparative column the datapoint appeared under,
    not the period it covers) -- see the comment on
    `normalizer._pick_fact_for_period`. Fact matching now keys off `end`, so
    fixtures must carry realistic `start`/`end` dates.
    """
    return [
        {
            "val": v,
            "fy": fy,
            "fp": "FY",
            "form": "10-K",
            "filed": filed,
            "start": f"{fy}-01-01",
            "end": f"{fy}-12-31",
            "accn": f"0000123456-{fy}-000001",
        }
        for v, fy, filed in entries
    ]


def _fixture_company_facts_three_year():
    # Hand-verified example: revenue 100 -> 133.1 over 3 years => CAGR = 10.0%
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": _rev_units(
                            [
                                (100, 2021, "2022-02-01"),
                                (110, 2022, "2023-02-01"),
                                (121, 2023, "2024-02-01"),
                                (133.1, 2024, "2025-02-01"),
                            ]
                        )
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": _rev_units(
                            [
                                (20, 2021, "2022-02-01"),
                                (22, 2022, "2023-02-01"),
                                (24.2, 2023, "2024-02-01"),
                                (26.62, 2024, "2025-02-01"),
                            ]
                        )
                    }
                },
                "NetIncomeLoss": {
                    "units": {"USD": _rev_units([(10, 2021, "2022-02-01"), (13.31, 2024, "2025-02-01")])}
                },
            }
        }
    }


def _fixture_company_facts_one_year():
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": _rev_units([(100, 2023, "2024-02-01")])}},
            }
        }
    }


# --- CAGR: hand-verified example --------------------------------------------


def test_cagr_hand_verified_100_to_133_1_over_3_years_is_10_pct():
    years = [
        YearValue(fiscal_year=2021, value=100, data_status="REPORTED"),
        YearValue(fiscal_year=2024, value=133.1, data_status="REPORTED"),
    ]
    result = compute_cagr(years, concept="revenue")
    assert result.insufficient_history is False
    assert result.num_years == 3
    assert result.cagr_pct == pytest.approx(10.0, abs=0.01)


def test_cagr_uses_earliest_and_latest_reported_points_only():
    years = [
        YearValue(fiscal_year=2021, value=100, data_status="REPORTED"),
        YearValue(fiscal_year=2022, value=999, data_status="MISSING"),  # ignored: not REPORTED
        YearValue(fiscal_year=2024, value=133.1, data_status="REPORTED"),
    ]
    result = compute_cagr(years, concept="revenue")
    assert result.start_year == 2021
    assert result.end_year == 2024
    assert result.cagr_pct == pytest.approx(10.0, abs=0.01)


# --- CAGR: insufficient history ---------------------------------------------


def test_cagr_insufficient_history_with_single_reported_year():
    years = [YearValue(fiscal_year=2023, value=100, data_status="REPORTED")]
    result = compute_cagr(years)
    assert result.insufficient_history is True
    assert result.cagr_pct is None
    assert "Fewer than 2" in result.reason


def test_cagr_insufficient_history_with_zero_reported_years():
    result = compute_cagr([])
    assert result.insufficient_history is True


def test_cagr_undefined_for_non_positive_start_value():
    years = [
        YearValue(fiscal_year=2021, value=-50, data_status="REPORTED"),
        YearValue(fiscal_year=2023, value=100, data_status="REPORTED"),
    ]
    result = compute_cagr(years)
    assert result.insufficient_history is True
    assert "negative" in result.reason


# --- margin trend -------------------------------------------------------------


def test_margin_trend_stats_min_max_latest_average():
    revenue = [
        YearValue(fiscal_year=2021, value=100, data_status="REPORTED"),
        YearValue(fiscal_year=2022, value=110, data_status="REPORTED"),
        YearValue(fiscal_year=2023, value=121, data_status="REPORTED"),
    ]
    op_income = [
        YearValue(fiscal_year=2021, value=20, data_status="REPORTED"),  # 20%
        YearValue(fiscal_year=2022, value=22, data_status="REPORTED"),  # 20%
        YearValue(fiscal_year=2023, value=30.25, data_status="REPORTED"),  # 25%
    ]
    result = compute_margin_trend(op_income, revenue, concept="operating_margin")
    assert result.insufficient_history is False
    assert result.min_margin_pct == pytest.approx(20.0)
    assert result.max_margin_pct == pytest.approx(25.0)
    assert result.latest_margin_pct == pytest.approx(25.0)
    assert result.average_margin_pct == pytest.approx((20 + 20 + 25) / 3)


def test_margin_trend_skips_years_missing_either_side():
    revenue = [
        YearValue(fiscal_year=2021, value=100, data_status="REPORTED"),
        YearValue(fiscal_year=2022, value=None, data_status="MISSING"),
    ]
    op_income = [
        YearValue(fiscal_year=2021, value=20, data_status="REPORTED"),
        YearValue(fiscal_year=2022, value=22, data_status="REPORTED"),
    ]
    result = compute_margin_trend(op_income, revenue)
    assert result.insufficient_history is True


# --- forward suggestion --------------------------------------------------------


def test_suggest_forward_growth_extrapolates_cagr_and_is_labeled_suggested():
    cagr = compute_cagr(
        [
            YearValue(fiscal_year=2021, value=100, data_status="REPORTED"),
            YearValue(fiscal_year=2024, value=133.1, data_status="REPORTED"),
        ]
    )
    forward = suggest_forward_growth(cagr, num_forecast_years=2)
    assert forward.insufficient_history is False
    assert forward.label == "SUGGESTED"
    assert forward.suggested_years == [2025, 2026]
    assert forward.suggested_values[0] == pytest.approx(133.1 * 1.10, rel=1e-3)
    assert "extrapolation" in forward.basis.lower()


def test_suggest_forward_growth_insufficient_history_passthrough():
    cagr = compute_cagr([])
    forward = suggest_forward_growth(cagr)
    assert forward.insufficient_history is True
    assert forward.suggested_values is None


# --- multi-year facts extraction: REPORTED vs MISSING preserved --------------


def test_normalize_company_facts_multi_year_preserves_reported_status_per_year():
    facts = _fixture_company_facts_three_year()
    rows = normalize_company_facts_multi_year(
        company_id=1,
        company_facts=facts,
        fiscal_years=[2021, 2022, 2023, 2024],
        concepts=["revenue"],
    )
    assert len(rows) == 4
    values_by_year = {r.fiscal_year: r for r in rows}
    assert values_by_year[2021].value == 100
    assert values_by_year[2021].data_status == DataStatus.REPORTED
    assert values_by_year[2024].value == 133.1
    assert values_by_year[2024].data_status == DataStatus.REPORTED


def test_normalize_company_facts_multi_year_missing_for_unfetched_year():
    facts = _fixture_company_facts_three_year()
    rows = normalize_company_facts_multi_year(
        company_id=1,
        company_facts=facts,
        fiscal_years=[2019, 2021],  # 2019 has no data
        concepts=["revenue"],
    )
    by_year = {r.fiscal_year: r for r in rows}
    assert by_year[2019].data_status == DataStatus.MISSING
    assert by_year[2019].value is None
    assert by_year[2021].data_status == DataStatus.REPORTED


def test_normalize_company_facts_multi_year_missing_for_unmatched_concept():
    facts = _fixture_company_facts_three_year()
    rows = normalize_company_facts_multi_year(
        company_id=1,
        company_facts=facts,
        fiscal_years=[2021],
        concepts=["capex"],  # not present in fixture at all
    )
    assert rows[0].data_status == DataStatus.MISSING
    assert rows[0].value is None


# --- end-to-end build_historical_trends ---------------------------------------


def test_build_historical_trends_end_to_end_hand_verified_cagr():
    facts = _fixture_company_facts_three_year()
    result = build_historical_trends(company_id=1, ticker="TEST", company_facts=facts, num_years=6)
    assert result["fiscal_years_covered"] == [2021, 2022, 2023, 2024]
    assert result["revenue_cagr"]["insufficient_history"] is False
    assert result["revenue_cagr"]["cagr_pct"] == pytest.approx(10.0, abs=0.01)
    assert result["suggested_forward_revenue"]["label"] == "SUGGESTED"
    assert result["operating_margin_trend"]["insufficient_history"] is False


def test_build_historical_trends_insufficient_history_for_single_year_company():
    facts = _fixture_company_facts_one_year()
    result = build_historical_trends(company_id=1, ticker="TEST", company_facts=facts, num_years=6)
    assert result["fiscal_years_covered"] == [2023]
    assert result["revenue_cagr"]["insufficient_history"] is True
    assert result["revenue_cagr"]["cagr_pct"] is None
    assert result["suggested_forward_revenue"]["insufficient_history"] is True
    assert result["suggested_forward_revenue"]["suggested_values"] is None

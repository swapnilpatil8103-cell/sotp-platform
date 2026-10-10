"""Valid hand-built ``inputs`` dicts for governed valuation runs, one per
method. Plain dicts (not model instances) because that is exactly what a
client sends to ``POST /valuation/run`` / ``execute_valuation_run``.

Numbers are deliberately simple, not claims about any real company.
"""

from __future__ import annotations

from typing import Any


def dcf_inputs(**overrides: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "base_revenue": 1000.0,
        "revenue_growth_rates": [0.10, 0.08],
        "ebit_margins": [0.20, 0.20],
        "tax_rate": 0.25,
        "da_pct_of_revenue": [0.05, 0.05],
        "capex_pct_of_revenue": [0.06, 0.06],
        "nwc_change_pct_of_revenue": [0.01, 0.01],
        "wacc": 0.09,
        "terminal_growth_rate": 0.02,
        "total_debt": 200.0,
        "cash_and_equivalents": 100.0,
        "investments": 0.0,
        "minority_interest": 0.0,
        "diluted_shares_outstanding": 100.0,
    }
    inputs.update(overrides)
    return inputs


def comps_inputs(**overrides: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "peers": [
            {"name": "PeerA", "revenue": 1000.0, "ebitda": 300.0, "ebit": 250.0, "net_income": 150.0,
             "market_cap": 3000.0, "debt": 400.0, "cash": 200.0},
            {"name": "PeerB", "revenue": 800.0, "ebitda": 200.0, "ebit": 160.0, "net_income": 100.0,
             "market_cap": 2000.0, "debt": 300.0, "cash": 100.0},
            {"name": "PeerC", "revenue": 1200.0, "ebitda": 350.0, "ebit": 290.0, "net_income": 180.0,
             "market_cap": 3600.0, "debt": 500.0, "cash": 250.0},
        ],
        "target_name": "TargetCo",
        "target_revenue": 900.0,
        "target_ebitda": 260.0,
        "target_ebit": 210.0,
        "target_net_income": 130.0,
        "target_debt": 350.0,
        "target_cash": 150.0,
        "target_diluted_shares_outstanding": 100.0,
        "chosen_multiple_metric": "ev_to_ebitda",
        "chosen_multiple_value": 11.0,
    }
    inputs.update(overrides)
    return inputs


def sotp_inputs(**overrides: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "segments": [
            {"name": "Services", "enterprise_value": 2000.0, "ownership_pct": 1.0},
            {"name": "Cloud", "enterprise_value": 800.0, "ownership_pct": 0.8},
        ],
        "cash_and_equivalents": 150.0,
        "marketable_securities": 50.0,
        "other_investments": 20.0,
        "total_debt": 300.0,
        "minority_interest": 0.0,
        "corporate_liabilities": 0.0,
        "corporate_overhead_annual": 10.0,
        "overhead_capitalization_multiple": 8.0,
        "corporate_overhead_treatment": "direct_deduction",
        "diluted_shares_outstanding": 100.0,
        "current_share_price": 25.0,
    }
    inputs.update(overrides)
    return inputs

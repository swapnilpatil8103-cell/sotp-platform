"""Pydantic request/response schemas for the deterministic valuation engine
(backend/valuation/). Every field that represents a material financial
assumption (growth rates, discount rates, margins, multiples, betas, etc.) is
required -- no silent defaults. Only cosmetic/technical parameters (e.g.
rounding precision) may default.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# WACC
# --------------------------------------------------------------------------


class WaccInput(BaseModel):
    risk_free_rate: float = Field(..., description="e.g. current 10y treasury yield, as a decimal (0.04 = 4%)")
    beta: float = Field(..., description="Company/segment levered beta")
    equity_risk_premium: float = Field(..., description="Market equity risk premium, as a decimal")
    cost_of_debt_pretax: float = Field(..., description="Pre-tax cost of debt, as a decimal")
    tax_rate: float = Field(..., description="Marginal/effective tax rate, as a decimal")
    market_value_equity: float = Field(..., gt=0, description="Market value of equity (E)")
    market_value_debt: float = Field(..., ge=0, description="Market value of debt (D)")


class WaccResult(BaseModel):
    inputs: WaccInput
    cost_of_equity: float
    cost_of_debt_after_tax: float
    equity_weight: float
    debt_weight: float
    wacc: float


# --------------------------------------------------------------------------
# DCF
# --------------------------------------------------------------------------


class DcfInput(BaseModel):
    base_revenue: float = Field(..., description="Most recent actual/reported revenue, the forecast base year")
    revenue_growth_rates: list[float] = Field(
        ..., min_length=1, description="Explicit YoY growth rate per forecast year, one entry per projected year"
    )
    ebit_margins: list[float] = Field(
        ..., min_length=1, description="Explicit EBIT margin per forecast year, same length as revenue_growth_rates"
    )
    tax_rate: float = Field(..., description="Marginal/effective tax rate applied to EBIT to get NOPAT")
    da_pct_of_revenue: list[float] = Field(..., min_length=1, description="D&A as % of revenue, per forecast year")
    capex_pct_of_revenue: list[float] = Field(..., min_length=1, description="CapEx as % of revenue, per forecast year")
    nwc_change_pct_of_revenue: list[float] = Field(
        ..., min_length=1, description="Change in net working capital as % of revenue, per forecast year"
    )
    wacc: float = Field(..., description="Discount rate applied to projected FCFF and terminal value")
    terminal_growth_rate: float = Field(..., description="Perpetuity growth rate (g) used in Gordon Growth terminal value")
    net_debt: float = Field(..., description="Total debt minus cash, for the EV->equity bridge")
    cash_and_equivalents: float = Field(..., description="Cash & equivalents added back in EV->equity bridge")
    investments: float = Field(0.0, description="Non-operating investments added in EV->equity bridge")
    minority_interest: float = Field(0.0, description="Minority interest deducted in EV->equity bridge")
    diluted_shares_outstanding: float = Field(..., gt=0)

    def validate_lengths(self) -> None:
        n = len(self.revenue_growth_rates)
        for name, seq in (
            ("ebit_margins", self.ebit_margins),
            ("da_pct_of_revenue", self.da_pct_of_revenue),
            ("capex_pct_of_revenue", self.capex_pct_of_revenue),
            ("nwc_change_pct_of_revenue", self.nwc_change_pct_of_revenue),
        ):
            if len(seq) != n:
                raise ValueError(f"{name} must have the same length as revenue_growth_rates ({n}), got {len(seq)}")


class DcfYearProjection(BaseModel):
    year_index: int
    revenue: float
    ebit: float
    nopat: float
    da: float
    capex: float
    nwc_change: float
    fcff: float
    discount_factor: float
    discounted_fcff: float


class DcfResult(BaseModel):
    inputs: DcfInput
    projections: list[DcfYearProjection]
    terminal_value_undiscounted: float
    terminal_value_discounted: float
    enterprise_value: float
    equity_value: float
    implied_price_per_share: float


# --------------------------------------------------------------------------
# Comps
# --------------------------------------------------------------------------


class CompPeer(BaseModel):
    name: str
    revenue: float
    ebitda: float
    ebit: float
    net_income: float
    market_cap: float
    debt: float
    cash: float
    book_value_equity: Optional[float] = None

    @property
    def enterprise_value(self) -> float:
        return self.market_cap + self.debt - self.cash


class PeerMultiples(BaseModel):
    name: str
    ev_to_revenue: Optional[float] = None
    ev_to_ebitda: Optional[float] = None
    ev_to_ebit: Optional[float] = None
    price_to_earnings: Optional[float] = None
    price_to_book: Optional[float] = None


class MultipleStats(BaseModel):
    metric: Literal["ev_to_revenue", "ev_to_ebitda", "ev_to_ebit", "price_to_earnings", "price_to_book"]
    min: float
    p25: float
    median: float
    p75: float
    max: float


class CompsInput(BaseModel):
    peers: list[CompPeer] = Field(..., min_length=1, description="Already-assembled peer set (peer selection is out of scope for this module)")
    target_name: str
    target_revenue: float
    target_ebitda: float
    target_ebit: float
    target_net_income: float
    target_book_value_equity: Optional[float] = None
    target_debt: float
    target_cash: float
    target_diluted_shares_outstanding: float = Field(..., gt=0)
    chosen_multiple_metric: Literal["ev_to_revenue", "ev_to_ebitda", "ev_to_ebit", "price_to_earnings", "price_to_book"]
    chosen_multiple_value: float = Field(..., description="The specific multiple value to apply, e.g. peer median EV/EBITDA")


class CompsResult(BaseModel):
    inputs: CompsInput
    peer_multiples: list[PeerMultiples]
    stats: list[MultipleStats]
    implied_enterprise_value: Optional[float] = None
    implied_equity_value: float
    implied_price_per_share: float


# --------------------------------------------------------------------------
# SOTP
# --------------------------------------------------------------------------


class SotpSegmentInput(BaseModel):
    name: str
    enterprise_value: float = Field(..., description="Segment's own pre-computed EV (from DCF or comps upstream)")
    ownership_pct: float = Field(..., gt=0, le=1.0, description="Company's ownership stake in this segment, 1.0 = fully owned/consolidated")

    @property
    def attributed_ev(self) -> float:
        return self.enterprise_value * self.ownership_pct


class SotpInput(BaseModel):
    segments: list[SotpSegmentInput] = Field(..., min_length=1)
    cash_and_equivalents: float
    marketable_securities: float = Field(0.0)
    other_investments: float = Field(0.0)
    total_debt: float
    minority_interest: float = Field(0.0)
    corporate_liabilities: float = Field(0.0, description="Other corporate-level liabilities beyond debt/minority interest")
    corporate_overhead_annual: float = Field(
        ..., description="Annual unallocated corporate overhead cost, used only under the capitalized_overhead treatment"
    )
    overhead_capitalization_multiple: float = Field(
        ..., gt=0, description="Multiple applied to annual corporate overhead to capitalize it into a deducted liability, e.g. 1/wacc or an explicit EV/EBITDA-style multiple"
    )
    corporate_overhead_treatment: Literal["direct_deduction", "capitalized_overhead"] = Field(
        ..., description="direct_deduction: no overhead capitalization deducted. capitalized_overhead: deduct corporate_overhead_annual * overhead_capitalization_multiple as a liability."
    )
    diluted_shares_outstanding: float = Field(..., gt=0)
    current_share_price: Optional[float] = Field(None, description="Current market price, if provided, for discount/premium comparison")


class SotpResult(BaseModel):
    inputs: SotpInput
    segment_attributed_evs: dict[str, float]
    sum_of_segment_evs: float
    non_operating_assets: float
    capitalized_overhead_deduction: float
    equity_value_direct_deduction: float
    equity_value_capitalized_overhead: float
    equity_value: float = Field(..., description="Equity value under the selected corporate_overhead_treatment")
    implied_price_per_share: float
    conglomerate_discount_pct: Optional[float] = Field(
        None, description="(implied - market) / market * 100 relative to current_share_price; negative = trading at a discount to SOTP"
    )
    upside_downside_pct: Optional[float] = Field(None, description="(implied - market) / market * 100; identical figure, named for the upside/downside framing")


# --------------------------------------------------------------------------
# Sensitivity
# --------------------------------------------------------------------------


class SensitivityInput(BaseModel):
    row_label: str = Field(..., description="Name of the row-axis variable, e.g. 'wacc'")
    row_values: list[float] = Field(..., min_length=1)
    col_label: str = Field(..., description="Name of the column-axis variable, e.g. 'terminal_growth_rate'")
    col_values: list[float] = Field(..., min_length=1)


class SensitivityResult(BaseModel):
    row_label: str
    row_values: list[float]
    col_label: str
    col_values: list[float]
    matrix: list[list[float]] = Field(..., description="matrix[i][j] = output for (row_values[i], col_values[j])")


# --------------------------------------------------------------------------
# Risk dashboard (Phase 8)
# --------------------------------------------------------------------------
#
# RiskCategoryScore lives in schemas (not backend/valuation/) specifically so
# that backend/ai/tasks/risk_explanation.py can import the shape without
# importing backend.valuation itself -- backend/ai/ must never import
# backend/valuation/ (see test_ai_import_boundary.py), even for a shared
# result type.

RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


class RiskCategoryScore(BaseModel):
    category: str
    score: RiskLevel
    explanation: str
    ai_assisted: bool = False

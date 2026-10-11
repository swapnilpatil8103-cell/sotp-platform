"""Pydantic request/response schemas for the deterministic valuation engine
(backend/valuation/). Every field that represents a material financial
assumption (growth rates, discount rates, margins, multiples, betas, etc.) is
required -- no silent defaults. Only cosmetic/technical parameters (e.g.
rounding precision) may default.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------
# WACC
# --------------------------------------------------------------------------


class WaccInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    risk_free_rate: float = Field(..., description="e.g. current 10y treasury yield, as a decimal (0.04 = 4%)")
    beta: float = Field(..., description="Company/segment levered beta")
    equity_risk_premium: float = Field(..., description="Market equity risk premium, as a decimal")
    cost_of_debt_pretax: float = Field(..., description="Pre-tax cost of debt, as a decimal")
    tax_rate: float = Field(..., ge=0, lt=1, description="Marginal/effective tax rate, as a decimal, 0 <= t < 1")
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
# Non-fatal validation warnings
# --------------------------------------------------------------------------


class ModelWarning(BaseModel):
    """A non-fatal observation about a result: the numbers are computed and returned,
    but an analyst should look at the flagged assumption. ``code`` is a stable
    identifier (for tests and the UI); ``message`` is the human explanation with the
    offending figures filled in."""

    code: str
    message: str


# --------------------------------------------------------------------------
# DCF
# --------------------------------------------------------------------------


# Per-year bounds for DCF schedules. Revenue cannot fall by more than 100% in a
# year (a growth rate <= -1 zeroes or flips the sign of revenue), and 1000%/yr
# is already far past any real forecast. An EBIT margin cannot exceed 100% of
# revenue; losses are legitimate, down to -500%.
GrowthRate = Annotated[float, Field(gt=-1, le=10)]
EbitMargin = Annotated[float, Field(ge=-5, le=1)]


class DcfInput(BaseModel):
    # NaN/Infinity are never valid inputs; left in, they flow through the maths and
    # come back as null outputs on a 200.
    model_config = ConfigDict(allow_inf_nan=False)

    base_revenue: float = Field(..., description="Most recent actual/reported revenue, the forecast base year")
    revenue_growth_rates: list[GrowthRate] = Field(
        ...,
        min_length=1,
        description="Explicit YoY growth rate per forecast year, one entry per projected year; each in (-1, 10]",
    )
    ebit_margins: list[EbitMargin] = Field(
        ...,
        min_length=1,
        description="Explicit EBIT margin per forecast year, same length as revenue_growth_rates; each in [-5, 1]",
    )
    tax_rate: float = Field(
        ..., ge=0, lt=1, description="Marginal/effective tax rate applied to EBIT to get NOPAT, 0 <= t < 1"
    )
    da_pct_of_revenue: list[float] = Field(..., min_length=1, description="D&A as % of revenue, per forecast year")
    capex_pct_of_revenue: list[float] = Field(..., min_length=1, description="CapEx as % of revenue, per forecast year")
    nwc_change_pct_of_revenue: Optional[list[float]] = Field(
        None,
        min_length=1,
        description=(
            "Change in net working capital as % of that year's revenue, per forecast year. Exactly one of "
            "this and nwc_pct_of_incremental_revenue must be supplied."
        ),
    )
    nwc_pct_of_incremental_revenue: Optional[float] = Field(
        None,
        ge=-1,
        le=1,
        description=(
            "Alternative to nwc_change_pct_of_revenue: NWC investment = this fraction of the YEAR-OVER-YEAR "
            "INCREASE in revenue (year 1 is measured against base_revenue), so a company that stops growing "
            "stops investing in working capital. A single rate for all years, e.g. 0.15 = 15 cents of NWC per "
            "extra dollar of revenue. The Gordon terminal value then uses a steady-state NWC investment "
            "(this fraction x revenue x terminal_growth_rate) rather than the last explicit year's."
        ),
    )
    wacc: float = Field(
        ..., gt=-1, description="Discount rate applied to projected FCFF and terminal value; must exceed -1 (1 + wacc is divided by)"
    )
    terminal_growth_rate: float = Field(
        ..., gt=-1, le=1, description="Perpetuity growth rate (g) used in Gordon Growth terminal value, in (-1, 1]"
    )
    total_debt: float = Field(..., description="Gross total debt (NOT net of cash), deducted in the EV->equity bridge")
    cash_and_equivalents: float = Field(..., description="Cash & equivalents added back in EV->equity bridge")
    investments: float = Field(0.0, description="Non-operating investments added in EV->equity bridge")
    minority_interest: float = Field(0.0, description="Minority interest deducted in EV->equity bridge")
    diluted_shares_outstanding: float = Field(..., gt=0)
    risk_free_rate: Optional[float] = Field(
        None,
        ge=-0.1,
        le=1,
        description=(
            "Optional, as a decimal. Used ONLY to raise a TERMINAL_GROWTH_ABOVE_RISK_FREE warning when "
            "terminal_growth_rate exceeds it; it never enters the valuation arithmetic."
        ),
    )
    exit_multiple: Optional[float] = Field(
        None,
        gt=0,
        le=1000,
        description=(
            "Optional explicit EV/EBITDA-style exit multiple applied to the terminal-year EBITDA "
            "(terminal-year EBIT + terminal-year D&A) to compute an independent Exit Multiple terminal "
            "value, alongside (never replacing) the Gordon Growth terminal value above. Omitted by "
            "default -- existing callers that never set this field see identical behavior/output shape "
            "as before (the new exit-multiple fields on DcfResult are simply left None)."
        ),
    )

    mid_year_convention: bool = Field(
        False,
        description=(
            "If true, each year's FCFF is discounted at t - 0.5 (cash flows arrive evenly through the "
            "year) instead of t, and the Gordon Growth terminal value is discounted at N - 0.5 to match "
            "(equivalent to TV * (1 + wacc)^0.5 discounted at N). The Exit Multiple terminal value is a "
            "year-end sale price and stays discounted at N. Default false: end-of-year discounting, "
            "output identical to before this flag existed."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_nwc_method(self) -> "DcfInput":
        has_schedule = self.nwc_change_pct_of_revenue is not None
        has_incremental = self.nwc_pct_of_incremental_revenue is not None
        if has_schedule and has_incremental:
            raise ValueError(
                "Provide only one NWC method: nwc_change_pct_of_revenue (% of revenue per year) OR "
                "nwc_pct_of_incremental_revenue (% of the increase in revenue), not both."
            )
        if not has_schedule and not has_incremental:
            raise ValueError(
                "Provide an NWC method: nwc_change_pct_of_revenue (a per-year schedule) or "
                "nwc_pct_of_incremental_revenue (a single rate on the increase in revenue)."
            )
        return self

    def validate_lengths(self) -> None:
        n = len(self.revenue_growth_rates)
        sequences = [
            ("ebit_margins", self.ebit_margins),
            ("da_pct_of_revenue", self.da_pct_of_revenue),
            ("capex_pct_of_revenue", self.capex_pct_of_revenue),
        ]
        if self.nwc_change_pct_of_revenue is not None:
            sequences.append(("nwc_change_pct_of_revenue", self.nwc_change_pct_of_revenue))
        for name, seq in sequences:
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

    # -- Optional, independently-computed Exit Multiple terminal value fields.
    # Populated only when inputs.exit_multiple is supplied; left None otherwise
    # so existing callers/tests that never set exit_multiple see byte-identical
    # DcfResult behavior on every field above (backward compatible).
    terminal_year_ebitda: Optional[float] = Field(
        None, description="terminal_year EBIT + terminal_year D&A; basis for the exit-multiple TV"
    )
    exit_multiple_terminal_value_undiscounted: Optional[float] = None
    exit_multiple_terminal_value_discounted: Optional[float] = None
    exit_multiple_enterprise_value: Optional[float] = Field(
        None, description="Enterprise value using the Exit Multiple terminal value instead of Gordon Growth"
    )
    exit_multiple_equity_value: Optional[float] = None
    exit_multiple_implied_price_per_share: Optional[float] = None

    # -- Additive diagnostics. Populated on every run; no existing field changes.
    terminal_fcff: Optional[float] = Field(
        None, description="FCFF_(n+1): the cash flow the Gordon Growth terminal value capitalises"
    )
    terminal_value_share_of_ev: Optional[float] = Field(
        None, description="Discounted Gordon terminal value / enterprise value (None if EV is not positive)"
    )
    warnings: list[ModelWarning] = Field(
        default_factory=list, description="Non-fatal validation warnings; empty when nothing looks off"
    )


# --------------------------------------------------------------------------
# Comps
# --------------------------------------------------------------------------


class CompPeer(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

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
    # How the peer set behind these statistics was formed. n_peers_used + n_not_meaningful +
    # n_outliers_excluded always equals the number of peers supplied.
    n_peers_used: Optional[int] = None
    n_not_meaningful: Optional[int] = Field(None, description="Peers with a non-positive/missing denominator for this multiple")
    n_outliers_excluded: Optional[int] = Field(None, description="Peers trimmed by the IQR rule for this multiple")


ExclusionReason = Literal["not_meaningful", "iqr_outlier_low", "iqr_outlier_high"]


class PeerExclusion(BaseModel):
    """One peer left out of one multiple's statistics, and why. Nothing is ever dropped without one of these."""

    peer: str
    metric: Literal["ev_to_revenue", "ev_to_ebitda", "ev_to_ebit", "price_to_earnings", "price_to_book"]
    reason: ExclusionReason
    value: Optional[float] = Field(None, description="The peer's multiple (None when it could not be computed)")
    lower_bound: Optional[float] = Field(None, description="IQR fence used (outlier reasons only)")
    upper_bound: Optional[float] = Field(None, description="IQR fence used (outlier reasons only)")
    detail: str


class CompsInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

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
    trim_outliers: bool = Field(
        False,
        description=(
            "If true, for each multiple separately, peers outside [Q1 - k*IQR, Q3 + k*IQR] are left out of that "
            "multiple's statistics (and listed in ``exclusions``). Needs at least 4 usable peers, and is skipped "
            "(with a warning) if it would leave fewer than 3. Never changes chosen_multiple_value."
        ),
    )
    iqr_multiplier: float = Field(1.5, gt=0, le=10, description="k in the IQR fence; 1.5 is the conventional value")


class CompsResult(BaseModel):
    inputs: CompsInput
    peer_multiples: list[PeerMultiples]
    stats: list[MultipleStats]
    implied_enterprise_value: Optional[float] = None
    implied_equity_value: float
    implied_price_per_share: float
    exclusions: list[PeerExclusion] = Field(
        default_factory=list,
        description="Every (peer, multiple) pair left out of the statistics, with the reason. Always reported.",
    )
    excluded_outlier_peer_count: int = Field(0, description="Distinct peers trimmed as IQR outliers on at least one multiple")
    warnings: list[ModelWarning] = Field(default_factory=list)


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
        None,
        description=(
            "(implied - market) / implied * 100: the market's discount to the SOTP-implied price. "
            "Positive = market trades BELOW SOTP value (discount); negative = ABOVE it (premium). "
            "None without a current_share_price or when the implied price is not positive."
        ),
    )
    upside_downside_pct: Optional[float] = Field(
        None, description="(implied - market) / market * 100: move from the market price needed to reach SOTP value"
    )
    warnings: list[ModelWarning] = Field(default_factory=list, description="Non-fatal validation warnings")


# --------------------------------------------------------------------------
# Sensitivity
# --------------------------------------------------------------------------


# The scalar numeric DcfInput fields a sensitivity grid may vary, and the
# always-populated scalar DcfResult fields it may read. Static Literals (not
# derived at runtime) so the OpenAPI schema lists them; a test asserts the
# input list matches DcfInput so the two cannot drift apart.
DcfScalarField = Literal[
    "base_revenue",
    "tax_rate",
    "wacc",
    "terminal_growth_rate",
    "total_debt",
    "cash_and_equivalents",
    "investments",
    "minority_interest",
    "diluted_shares_outstanding",
    "exit_multiple",
    "nwc_pct_of_incremental_revenue",
]
DcfSensitivityOutput = Literal[
    "enterprise_value",
    "equity_value",
    "implied_price_per_share",
    "terminal_value_undiscounted",
    "terminal_value_discounted",
]


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
